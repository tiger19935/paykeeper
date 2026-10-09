"""Recovery for stale `in_progress` idempotency keys.

Flow for a charge: ask the provider whether an operation with our
operation id exists. If so, back-fill the local state (ledger + charge
row + mark key completed) from the provider record. If not, retry the
charge. Either way we never double-charge.

See `docs/adr/0002-no-retry-on-ambiguous-outcome.md`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.api.errors import (
    ProviderUnavailableError as HTTPProviderUnavailable,
)
from paykeeper.domain.enums import (
    ChargeState,
    LedgerEntryType,
    OutboxEventType,
    RefundState,
)
from paykeeper.domain.models import (
    Charge,
    IdempotencyKey,
    LedgerEntry,
    OutboxEvent,
    Refund,
)
from paykeeper.idempotency.keys import complete_key
from paykeeper.providers.base import (
    ChargeResult,
    Provider,
    ProviderError,
    RefundResult,
)


def _charge_response(charge: Charge) -> dict[str, Any]:
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


def _refund_response(refund: Refund) -> dict[str, Any]:
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


async def _refresh_lock(session: AsyncSession, key: str, scope: str) -> None:
    from datetime import UTC, datetime

    row = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.scope == scope)
        )
    ).scalar_one()
    row.locked_at = datetime.now(UTC)
    await session.commit()


async def recover_charge(
    session: AsyncSession,
    provider: Provider,
    *,
    key: str,
    scope: str,
    operation_id: str,
    request_body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    await _refresh_lock(session, key, scope)

    try:
        prior = await provider.get_by_operation_id(operation_id=operation_id)
    except ProviderError as exc:
        raise HTTPProviderUnavailable(f"recovery lookup failed: {exc}") from exc

    if prior is None or not isinstance(prior, ChargeResult):
        # Nothing exists at the provider — remove the stale key and
        # re-run the normal flow.
        await session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.scope == scope)
        )
        await session.commit()
        from datetime import timedelta

        from paykeeper.domain.service import process_charge

        return await process_charge(
            session,
            provider,
            idempotency_key=key,
            body=request_body,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )

    existing = (
        await session.execute(select(Charge).where(Charge.operation_id == operation_id))
    ).scalar_one_or_none()
    if existing is None:
        charge = Charge(
            id=uuid.uuid4(),
            customer_id=request_body["customer_id"],
            amount=prior.amount,
            currency=prior.currency,
            payment_method_token=request_body["payment_method_token"],
            description=request_body.get("description"),
            state=ChargeState.SUCCEEDED.value,
            provider=provider.name,
            provider_ref=prior.provider_ref,
            operation_id=operation_id,
        )
        session.add(charge)
        session.add(
            LedgerEntry(
                id=uuid.uuid4(),
                entry_type=LedgerEntryType.CHARGE_SUCCEEDED.value,
                amount=prior.amount,
                currency=prior.currency,
                charge_id=charge.id,
                provider=provider.name,
                provider_ref=prior.provider_ref,
            )
        )
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type=OutboxEventType.CHARGE_SUCCEEDED.value,
                aggregate_id=charge.id,
                payload=_charge_response(charge) | {"recovered": True},
            )
        )
        await session.flush()
        existing = charge

    response = _charge_response(existing)
    await complete_key(
        session,
        key=key,
        scope=scope,
        response_status=201,
        response_body=response,
        resource_id=existing.id,
    )
    await session.commit()
    return 201, response


async def recover_refund(
    session: AsyncSession,
    provider: Provider,
    *,
    key: str,
    scope: str,
    operation_id: str,
    charge: Charge,
    request_body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    await _refresh_lock(session, key, scope)
    try:
        prior = await provider.get_by_operation_id(operation_id=operation_id)
    except ProviderError as exc:
        raise HTTPProviderUnavailable(f"recovery lookup failed: {exc}") from exc

    if prior is None or not isinstance(prior, RefundResult):
        await session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.scope == scope)
        )
        await session.commit()
        from datetime import timedelta

        from paykeeper.domain import service as _service

        process_refund = _service.process_refund  # resolved at call time
        return await process_refund(
            session,
            provider,
            idempotency_key=key,
            body=request_body,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )

    existing = (
        await session.execute(select(Refund).where(Refund.operation_id == operation_id))
    ).scalar_one_or_none()
    if existing is None:
        refund = Refund(
            id=uuid.uuid4(),
            charge_id=charge.id,
            amount=prior.amount,
            currency=prior.currency,
            reason=request_body.get("reason"),
            state=RefundState.SUCCEEDED.value,
            provider=provider.name,
            provider_ref=prior.provider_ref,
            operation_id=operation_id,
        )
        session.add(refund)
        session.add(
            LedgerEntry(
                id=uuid.uuid4(),
                entry_type=LedgerEntryType.REFUND_SUCCEEDED.value,
                amount=prior.amount,
                currency=prior.currency,
                charge_id=charge.id,
                refund_id=refund.id,
                provider=provider.name,
                provider_ref=prior.provider_ref,
            )
        )
        session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type=OutboxEventType.REFUND_SUCCEEDED.value,
                aggregate_id=refund.id,
                payload=_refund_response(refund) | {"recovered": True},
            )
        )
        await session.flush()
        existing = refund

    response = _refund_response(existing)
    await complete_key(
        session,
        key=key,
        scope=scope,
        response_status=201,
        response_body=response,
        resource_id=existing.id,
    )
    await session.commit()
    return 201, response
