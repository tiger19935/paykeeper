"""Service functions that implement the HTTP endpoints' behavior.

These functions are intentionally not FastAPI-aware: they take a session
and a provider, so they can be exercised directly from integration tests.
Routes are thin adapters that marshal the request and dispatch.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.api.errors import (
    CardDeclinedError as HTTPCardDeclinedError,
)
from paykeeper.api.errors import (
    IdempotencyInFlightError,
    IdempotencyMismatchError,
)
from paykeeper.api.errors import (
    ProviderUnavailableError as HTTPProviderUnavailable,
)
from paykeeper.domain.enums import (
    ChargeState,
    IdempotencyScope,
    LedgerEntryType,
    OutboxEventType,
)
from paykeeper.domain.models import Charge, LedgerEntry, OutboxEvent
from paykeeper.idempotency import claim_or_replay, complete_key, fingerprint
from paykeeper.idempotency.keys import IdempotencyOutcome, IdempotencyOutcomeKind
from paykeeper.providers.backoff import with_retries
from paykeeper.providers.base import (
    CardDeclinedError,
    ChargeResult,
    InvalidRequestError,
    Provider,
    ProviderError,
)

_IN_FLIGHT_POLL_INTERVAL_MS = 25
_IN_FLIGHT_POLL_ATTEMPTS = 60


async def _resolve_idempotency(
    session: AsyncSession,
    *,
    key: str,
    scope: str,
    fp: str,
    stale_after: timedelta,
    ttl: timedelta,
) -> IdempotencyOutcome:
    """Call claim_or_replay with a bounded wait on IN_FLIGHT.

    Concurrent callers of the same key race on the INSERT; one wins, the
    others see IN_FLIGHT. They poll until the winner commits REPLAY state,
    or until the budget is spent (then 409 in-flight).
    """

    for _ in range(_IN_FLIGHT_POLL_ATTEMPTS):
        outcome = await claim_or_replay(
            session,
            key=key,
            scope=scope,
            request_fingerprint=fp,
            stale_after=stale_after,
            ttl=ttl,
        )
        if outcome.kind is not IdempotencyOutcomeKind.IN_FLIGHT:
            return outcome
        await asyncio.sleep(_IN_FLIGHT_POLL_INTERVAL_MS / 1000)
    return outcome


def _charge_to_dict(charge: Charge) -> dict[str, Any]:
    return {
        "id": str(charge.id),
        "customer_id": charge.customer_id,
        "amount": charge.amount,
        "currency": charge.currency,
        "status": charge.state,
        "provider": charge.provider,
        "provider_ref": charge.provider_ref,
        "description": charge.description,
        "created_at": charge.created_at.isoformat() if charge.created_at else None,
    }


async def process_charge(
    session: AsyncSession,
    provider: Provider,
    *,
    idempotency_key: str,
    body: dict[str, Any],
    stale_after: timedelta,
    ttl: timedelta,
    retry_max_attempts: int = 4,
    retry_base_delay_ms: int = 50,
    retry_max_delay_ms: int = 2000,
) -> tuple[int, dict[str, Any]]:
    scope = f"{IdempotencyScope.CHARGE.value}:{body['customer_id']}"
    fp = fingerprint(body)

    outcome = await _resolve_idempotency(
        session,
        key=idempotency_key,
        scope=scope,
        fp=fp,
        stale_after=stale_after,
        ttl=ttl,
    )

    if outcome.kind is IdempotencyOutcomeKind.IN_FLIGHT:
        raise IdempotencyInFlightError("a request with this key is still in flight")
    if outcome.kind is IdempotencyOutcomeKind.MISMATCH:
        raise IdempotencyMismatchError("idempotency key reused with a different request body")
    if outcome.kind is IdempotencyOutcomeKind.REPLAY:
        assert outcome.response_body is not None
        assert outcome.response_status is not None
        return outcome.response_status, outcome.response_body
    if outcome.kind is IdempotencyOutcomeKind.STALE:
        # The recovery path is implemented in paykeeper.idempotency.recovery.
        from paykeeper.idempotency.recovery import recover_charge  # local import to avoid cycle

        return await recover_charge(
            session,
            provider,
            key=idempotency_key,
            scope=scope,
            operation_id=outcome.operation_id,
            request_body=body,
        )

    assert outcome.kind is IdempotencyOutcomeKind.NEW

    async def _call_provider() -> ChargeResult:
        return await provider.charge(
            amount=body["amount"],
            currency=body["currency"],
            payment_method_token=body["payment_method_token"],
            customer_id=body["customer_id"],
            operation_id=outcome.operation_id,
        )

    try:
        charge_result = await with_retries(
            _call_provider,
            max_attempts=retry_max_attempts,
            base_delay_ms=retry_base_delay_ms,
            max_delay_ms=retry_max_delay_ms,
        )
    except CardDeclinedError as exc:
        charge = Charge(
            id=uuid.uuid4(),
            customer_id=body["customer_id"],
            amount=body["amount"],
            currency=body["currency"],
            payment_method_token=body["payment_method_token"],
            description=body.get("description"),
            state=ChargeState.FAILED.value,
            provider=provider.name,
            provider_ref=None,
            operation_id=outcome.operation_id,
            failure_reason=str(exc) or "card_declined",
        )
        session.add(charge)
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type=OutboxEventType.CHARGE_FAILED.value,
                aggregate_id=charge.id,
                payload=_charge_to_dict(charge) | {"reason": charge.failure_reason},
            )
        )
        await session.flush()
        response = _charge_to_dict(charge)
        await complete_key(
            session,
            key=idempotency_key,
            scope=scope,
            response_status=402,
            response_body=response,
            resource_id=charge.id,
        )
        await session.commit()
        raise HTTPCardDeclinedError(charge.failure_reason or "card_declined") from exc
    except InvalidRequestError as exc:
        await session.rollback()
        # Delete the key row so a corrected request can be made.
        from sqlalchemy import delete

        from paykeeper.domain.models import IdempotencyKey as _Key

        await session.execute(delete(_Key).where(_Key.key == idempotency_key, _Key.scope == scope))
        await session.commit()
        raise HTTPCardDeclinedError(f"invalid request: {exc}") from exc
    except ProviderError as exc:
        # Transient failure. Leave the key row as in_progress so recovery
        # (phase 11) can decide; surface 502 to the client.
        raise HTTPProviderUnavailable(str(exc) or "provider unavailable") from exc

    charge = Charge(
        id=uuid.uuid4(),
        customer_id=body["customer_id"],
        amount=body["amount"],
        currency=body["currency"],
        payment_method_token=body["payment_method_token"],
        description=body.get("description"),
        state=ChargeState.SUCCEEDED.value,
        provider=provider.name,
        provider_ref=charge_result.provider_ref,
        operation_id=outcome.operation_id,
    )
    session.add(charge)
    session.add(
        LedgerEntry(
            id=uuid.uuid4(),
            entry_type=LedgerEntryType.CHARGE_SUCCEEDED.value,
            amount=charge.amount,
            currency=charge.currency,
            charge_id=charge.id,
            provider=charge.provider,
            provider_ref=charge.provider_ref,
        )
    )
    session.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type=OutboxEventType.CHARGE_SUCCEEDED.value,
            aggregate_id=charge.id,
            payload=_charge_to_dict(charge),
        )
    )

    await session.flush()
    response = _charge_to_dict(charge)
    await complete_key(
        session,
        key=idempotency_key,
        scope=scope,
        response_status=201,
        response_body=response,
        resource_id=charge.id,
    )
    await session.commit()
    return 201, response


async def process_refund(
    session: AsyncSession,
    provider: Provider,
    *,
    idempotency_key: str,
    body: dict[str, Any],
    stale_after: timedelta,
    ttl: timedelta,
) -> tuple[int, dict[str, Any]]:
    raise NotImplementedError("process_refund is implemented in a later commit")


async def get_charge(session: AsyncSession, charge_id: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    try:
        oid = uuid.UUID(charge_id)
    except ValueError:
        return None
    row = (await session.execute(select(Charge).where(Charge.id == oid))).scalar_one_or_none()
    return _charge_to_dict(row) if row else None
