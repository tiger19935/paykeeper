"""Idempotency-Key handling.

The claim step inserts the key row in its own short transaction, BEFORE any
provider call. See `docs/adr/0001-persist-key-before-provider-call.md` for
why — persisting after the provider call opens a window where a crash leaves
us having charged a card we don't remember charging.

A stale `in_progress` row (older than `stale_after`) means a prior worker
died mid-flight. The caller must then route to the recovery module, which
asks the provider "did my operation id go through?" before deciding what to
do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.enums import IdempotencyState
from paykeeper.domain.models import IdempotencyKey


class IdempotencyOutcomeKind(StrEnum):
    NEW = "new"
    REPLAY = "replay"
    MISMATCH = "mismatch"
    IN_FLIGHT = "in_flight"
    STALE = "stale"


@dataclass(slots=True)
class IdempotencyOutcome:
    kind: IdempotencyOutcomeKind
    operation_id: str
    response_status: int | None = None
    response_body: dict[str, object] | None = None
    resource_id: uuid.UUID | None = None


def _derive_operation_id(key: str, scope: str) -> str:
    namespace = uuid.UUID("5f6b0b1e-6a53-4b7a-9b2d-6f8c6b23f7c1")
    return str(uuid.uuid5(namespace, f"{scope}:{key}"))


async def claim_or_replay(
    session: AsyncSession,
    *,
    key: str,
    scope: str,
    request_fingerprint: str,
    stale_after: timedelta,
    ttl: timedelta,
    now: datetime | None = None,
) -> IdempotencyOutcome:
    now = now or datetime.now(UTC)
    expires_at = now + ttl
    operation_id = _derive_operation_id(key, scope)

    stmt = (
        pg_insert(IdempotencyKey)
        .values(
            key=key,
            scope=scope,
            request_fingerprint=request_fingerprint,
            state=IdempotencyState.IN_PROGRESS.value,
            locked_at=now,
            created_at=now,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    )
    inserted = (await session.execute(stmt)).first()
    await session.commit()

    if inserted is not None:
        return IdempotencyOutcome(IdempotencyOutcomeKind.NEW, operation_id=operation_id)

    existing = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.scope == scope)
        )
    ).scalar_one()

    if existing.expires_at <= now:
        await session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.key == key,
                IdempotencyKey.scope == scope,
                IdempotencyKey.expires_at <= now,
            )
        )
        await session.commit()
        return await claim_or_replay(
            session,
            key=key,
            scope=scope,
            request_fingerprint=request_fingerprint,
            stale_after=stale_after,
            ttl=ttl,
            now=now,
        )

    if existing.request_fingerprint != request_fingerprint:
        return IdempotencyOutcome(IdempotencyOutcomeKind.MISMATCH, operation_id=operation_id)

    if existing.state == IdempotencyState.COMPLETED.value:
        return IdempotencyOutcome(
            IdempotencyOutcomeKind.REPLAY,
            operation_id=operation_id,
            response_status=existing.response_status,
            response_body=existing.response_body,
            resource_id=existing.resource_id,
        )

    if now - existing.locked_at > stale_after:
        return IdempotencyOutcome(
            IdempotencyOutcomeKind.STALE,
            operation_id=operation_id,
            resource_id=existing.resource_id,
        )

    return IdempotencyOutcome(IdempotencyOutcomeKind.IN_FLIGHT, operation_id=operation_id)


async def complete_key(
    session: AsyncSession,
    *,
    key: str,
    scope: str,
    response_status: int,
    response_body: dict[str, object],
    resource_id: uuid.UUID,
) -> None:
    """Mark a claimed key as completed.

    Must run inside the same transaction as the money-moving writes so
    that either both commit together or neither does.
    """

    row = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.key == key, IdempotencyKey.scope == scope)
        )
    ).scalar_one()
    row.state = IdempotencyState.COMPLETED.value
    row.response_status = response_status
    row.response_body = response_body
    row.resource_id = resource_id
