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
    ChargeNotFoundError,
    IdempotencyInFlightError,
    IdempotencyMismatchError,
    RefundExceedsBalanceError,
)
from paykeeper.api.errors import (
    ProviderUnavailableError as HTTPProviderUnavailable,
)
from paykeeper.domain.enums import (
    ChargeState,
    IdempotencyScope,
    LedgerEntryType,
    OutboxEventType,
    RefundState,
)
from paykeeper.domain.models import Charge, LedgerEntry, OutboxEvent, Refund
from paykeeper.idempotency import claim_or_replay, complete_key, fingerprint
from paykeeper.idempotency.keys import IdempotencyOutcome, IdempotencyOutcomeKind
from paykeeper.providers.base import (
    CardDeclinedError,
    InvalidRequestError,
    ProviderError,
)
from paykeeper.providers.router import ProviderRouter

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
    router: ProviderRouter,
    *,
    idempotency_key: str,
    body: dict[str, Any],
    stale_after: timedelta,
    ttl: timedelta,
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
            router,
            key=idempotency_key,
            scope=scope,
            operation_id=outcome.operation_id,
            request_body=body,
        )

    assert outcome.kind is IdempotencyOutcomeKind.NEW

    try:
        charge_result, used_provider = await router.charge(
            amount=body["amount"],
            currency=body["currency"],
            payment_method_token=body["payment_method_token"],
            customer_id=body["customer_id"],
            operation_id=outcome.operation_id,
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
            provider=router.primary.name,
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
        provider=used_provider,
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


def _refund_to_dict(refund: Refund) -> dict[str, Any]:
    return {
        "id": str(refund.id),
        "charge_id": str(refund.charge_id),
        "amount": refund.amount,
        "currency": refund.currency,
        "status": refund.state,
        "provider": refund.provider,
        "provider_ref": refund.provider_ref,
        "reason": refund.reason,
        "created_at": refund.created_at.isoformat() if refund.created_at else None,
    }


async def process_refund(
    session: AsyncSession,
    router: ProviderRouter,
    *,
    idempotency_key: str,
    body: dict[str, Any],
    stale_after: timedelta,
    ttl: timedelta,
) -> tuple[int, dict[str, Any]]:
    from sqlalchemy import func, select, text

    scope = f"{IdempotencyScope.REFUND.value}:{body['charge_id']}"
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

    try:
        charge_uuid = uuid.UUID(body["charge_id"])
    except ValueError as exc:
        raise ChargeNotFoundError(f"charge {body['charge_id']} not found") from exc

    if outcome.kind is IdempotencyOutcomeKind.STALE:
        charge_row = (
            await session.execute(select(Charge).where(Charge.id == charge_uuid))
        ).scalar_one_or_none()
        if charge_row is None:
            raise ChargeNotFoundError(f"charge {body['charge_id']} not found")
        from paykeeper.idempotency.recovery import recover_refund

        return await recover_refund(
            session,
            router,
            key=idempotency_key,
            scope=scope,
            operation_id=outcome.operation_id,
            charge=charge_row,
            request_body=body,
        )

    assert outcome.kind is IdempotencyOutcomeKind.NEW

    # Money-moving transaction. READ COMMITTED + `SELECT ... FOR UPDATE`
    # on the charge row: concurrent refunds race on the lock, and when
    # the loser unblocks it reads the committed state of the refunds
    # table (so the newly-inserted sibling refund is included in the
    # balance recomputation). REPEATABLE READ would read a pre-lock
    # snapshot and let the loser compute a stale remaining-balance.
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))

    charge = (
        await session.execute(select(Charge).where(Charge.id == charge_uuid).with_for_update())
    ).scalar_one_or_none()
    if charge is None:
        raise ChargeNotFoundError(f"charge {body['charge_id']} not found")
    if charge.state != ChargeState.SUCCEEDED.value:
        raise RefundExceedsBalanceError("charge is not in a refundable state")

    refunded_so_far = (
        await session.execute(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.charge_id == charge_uuid,
                Refund.state == RefundState.SUCCEEDED.value,
            )
        )
    ).scalar_one()
    remaining = charge.amount - int(refunded_so_far)

    if body["amount"] > remaining:
        raise RefundExceedsBalanceError(
            f"refund amount {body['amount']} exceeds remaining {remaining}"
        )

    try:
        refund_result = await router.refund(
            on_provider=charge.provider,
            charge_provider_ref=charge.provider_ref or "",
            amount=body["amount"],
            currency=charge.currency,
            operation_id=outcome.operation_id,
        )
    except InvalidRequestError as exc:
        await session.rollback()
        from sqlalchemy import delete

        from paykeeper.domain.models import IdempotencyKey as _Key

        await session.execute(delete(_Key).where(_Key.key == idempotency_key, _Key.scope == scope))
        await session.commit()
        raise RefundExceedsBalanceError(f"invalid refund: {exc}") from exc
    except ProviderError as exc:
        from paykeeper.api.errors import ProviderUnavailableError as HTTPProviderUnavailable

        raise HTTPProviderUnavailable(str(exc) or "provider unavailable") from exc

    refund = Refund(
        id=uuid.uuid4(),
        charge_id=charge.id,
        amount=body["amount"],
        currency=charge.currency,
        reason=body.get("reason"),
        state=RefundState.SUCCEEDED.value,
        provider=charge.provider,
        provider_ref=refund_result.provider_ref,
        operation_id=outcome.operation_id,
    )
    session.add(refund)
    await session.flush()
    session.add(
        LedgerEntry(
            id=uuid.uuid4(),
            entry_type=LedgerEntryType.REFUND_SUCCEEDED.value,
            amount=refund.amount,
            currency=refund.currency,
            charge_id=charge.id,
            refund_id=refund.id,
            provider=refund.provider,
            provider_ref=refund.provider_ref,
        )
    )
    session.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type=OutboxEventType.REFUND_SUCCEEDED.value,
            aggregate_id=refund.id,
            payload=_refund_to_dict(refund),
        )
    )
    await session.flush()
    response = _refund_to_dict(refund)
    await complete_key(
        session,
        key=idempotency_key,
        scope=scope,
        response_status=201,
        response_body=response,
        resource_id=refund.id,
    )
    await session.commit()
    return 201, response


async def get_refund(session: AsyncSession, refund_id: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    try:
        rid = uuid.UUID(refund_id)
    except ValueError:
        return None
    row = (await session.execute(select(Refund).where(Refund.id == rid))).scalar_one_or_none()
    return _refund_to_dict(row) if row else None


async def get_charge(session: AsyncSession, charge_id: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    try:
        oid = uuid.UUID(charge_id)
    except ValueError:
        return None
    row = (await session.execute(select(Charge).where(Charge.id == oid))).scalar_one_or_none()
    return _charge_to_dict(row) if row else None
