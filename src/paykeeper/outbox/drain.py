"""Outbox drain.

Behavior:
- Takes a Postgres advisory lock so two drains can't run at once.
- Selects unpublished events (`published_at IS NULL`), ordered by id.
- For each: writes a line of JSON to stdout, updates `published_at`.
- Commits in batches of `batch_size`.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TextIO

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import OutboxEvent

_LOCK_KEY = 920_250_709  # arbitrary but stable


async def drain_once(
    session: AsyncSession,
    *,
    out: TextIO | None = None,
    batch_size: int = 100,
) -> int:
    """Drain one pass. Returns the number of events published.

    A `pg_try_advisory_xact_lock` is taken inside each batch's transaction,
    so two drains cannot interleave batches, and the lock is auto-released
    on COMMIT. Session-level advisory locks would leak if the Postgres
    connection were returned to a pool without an explicit unlock.
    """

    out = out or sys.stdout
    total = 0
    while True:
        locked = (
            await session.execute(text(f"SELECT pg_try_advisory_xact_lock({_LOCK_KEY})"))
        ).scalar_one()
        if not locked:
            await session.rollback()
            return total

        rows = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at, OutboxEvent.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            await session.commit()
            break
        now = datetime.now(UTC)
        for ev in rows:
            line = json.dumps(
                {
                    "id": str(ev.id),
                    "event_type": ev.event_type,
                    "aggregate_id": str(ev.aggregate_id),
                    "payload": ev.payload,
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            out.write(line + "\n")
            ev.published_at = now
            total += 1
        await session.commit()
    out.flush()
    return total
