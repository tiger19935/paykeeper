from __future__ import annotations

import io
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import OutboxEvent
from paykeeper.outbox.drain import drain_once

pytestmark = pytest.mark.integration


async def test_drain_publishes_then_marks_published(db_session: AsyncSession) -> None:
    for i in range(3):
        db_session.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type=f"test.event_{i}",
                aggregate_id=uuid.uuid4(),
                payload={"i": i},
            )
        )
    await db_session.commit()

    buf = io.StringIO()
    published = await drain_once(db_session, out=buf, batch_size=10)
    assert published == 3

    lines = [ln for ln in buf.getvalue().splitlines() if ln]
    assert len(lines) == 3
    for line in lines:
        rec = json.loads(line)
        assert rec["event_type"].startswith("test.event_")

    rows = (
        await db_session.execute(select(OutboxEvent))
    ).scalars().all()
    assert all(r.published_at is not None for r in rows)


async def test_drain_is_idempotent(db_session: AsyncSession) -> None:
    db_session.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="test.once",
            aggregate_id=uuid.uuid4(),
            payload={},
        )
    )
    await db_session.commit()

    first = await drain_once(db_session, out=io.StringIO())
    second = await drain_once(db_session, out=io.StringIO())
    assert first == 1
    assert second == 0
