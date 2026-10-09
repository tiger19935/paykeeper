"""Dispatch verified webhook events into local state transitions.

Dedupe is UNIQUE(provider, provider_event_id): a second delivery of the
same event id is a no-op commit. Unknown event types are stored and
acknowledged so Stripe (or any publisher) stops retrying.

State transitions are idempotent: applying `charge.succeeded` twice to a
charge already succeeded is a no-op.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.enums import ChargeState, RefundState
from paykeeper.domain.models import Charge, Refund, WebhookEvent


async def apply_webhook(
    session: AsyncSession,
    *,
    provider: str,
    body: bytes,
) -> tuple[str, bool]:
    """Dispatch a verified webhook payload.

    Returns `(status, applied)` where `status` is a brief machine tag
    and `applied` is True if this delivery changed any local state.
    """

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"webhook body is not valid JSON: {exc}") from exc

    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if not event_id or not event_type:
        raise ValueError("webhook payload missing id/type")

    inserted = (
        await session.execute(
            pg_insert(WebhookEvent)
            .values(
                id=uuid.uuid4(),
                provider=provider,
                provider_event_id=event_id,
                event_type=event_type,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(WebhookEvent.id)
        )
    ).first()

    if inserted is None:
        # Duplicate delivery — do nothing but return 200 OK.
        return "duplicate", False

    applied = await _apply_transition(session, provider, event_type, payload)
    event_row = (
        await session.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == provider,
                WebhookEvent.provider_event_id == event_id,
            )
        )
    ).scalar_one()
    event_row.processed_at = datetime.now(UTC)
    await session.commit()
    return ("applied" if applied else "stored"), applied


async def _apply_transition(
    session: AsyncSession,
    provider: str,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    data = payload.get("data") or {}
    if event_type == "charge.succeeded":
        return await _transition_charge(session, provider, data, ChargeState.SUCCEEDED)
    if event_type == "charge.failed":
        return await _transition_charge(session, provider, data, ChargeState.FAILED)
    if event_type == "refund.succeeded":
        return await _transition_refund(session, provider, data, RefundState.SUCCEEDED)
    return False


async def _transition_charge(
    session: AsyncSession,
    provider: str,
    data: dict[str, Any],
    new_state: ChargeState,
) -> bool:
    provider_ref = data.get("provider_ref") or data.get("id")
    if not provider_ref:
        return False
    charge = (
        await session.execute(
            select(Charge).where(
                Charge.provider == provider, Charge.provider_ref == str(provider_ref)
            )
        )
    ).scalar_one_or_none()
    if charge is None:
        return False
    if charge.state == new_state.value:
        return False
    charge.state = new_state.value
    return True


async def _transition_refund(
    session: AsyncSession,
    provider: str,
    data: dict[str, Any],
    new_state: RefundState,
) -> bool:
    provider_ref = data.get("provider_ref") or data.get("id")
    if not provider_ref:
        return False
    refund = (
        await session.execute(
            select(Refund).where(
                Refund.provider == provider, Refund.provider_ref == str(provider_ref)
            )
        )
    ).scalar_one_or_none()
    if refund is None:
        return False
    if refund.state == new_state.value:
        return False
    refund.state = new_state.value
    return True
