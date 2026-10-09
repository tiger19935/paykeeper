from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.idempotency import claim_or_replay, complete_key, fingerprint
from paykeeper.idempotency.keys import IdempotencyOutcomeKind

pytestmark = pytest.mark.integration


async def _claim(session: AsyncSession, key: str, body: dict[str, object]):
    return await claim_or_replay(
        session,
        key=key,
        scope="charge:cust_1",
        request_fingerprint=fingerprint(body),
        stale_after=timedelta(seconds=30),
        ttl=timedelta(hours=24),
    )


async def test_new_then_replay(db_session: AsyncSession) -> None:
    body = {"amount": 100, "currency": "USD"}

    first = await _claim(db_session, "k1", body)
    assert first.kind is IdempotencyOutcomeKind.NEW

    # Simulate completion of the operation.
    import uuid

    resource_id = uuid.uuid4()
    await complete_key(
        db_session,
        key="k1",
        scope="charge:cust_1",
        response_status=201,
        response_body={"id": str(resource_id), "amount": 100},
        resource_id=resource_id,
    )
    await db_session.commit()

    second = await _claim(db_session, "k1", body)
    assert second.kind is IdempotencyOutcomeKind.REPLAY
    assert second.response_status == 201
    assert second.response_body == {"id": str(resource_id), "amount": 100}


async def test_same_key_different_body_mismatches(db_session: AsyncSession) -> None:
    body = {"amount": 100, "currency": "USD"}
    other = {"amount": 999, "currency": "USD"}

    first = await _claim(db_session, "k2", body)
    assert first.kind is IdempotencyOutcomeKind.NEW

    second = await _claim(db_session, "k2", other)
    assert second.kind is IdempotencyOutcomeKind.MISMATCH


async def test_in_progress_without_stale_is_in_flight(db_session: AsyncSession) -> None:
    body = {"amount": 100, "currency": "USD"}
    first = await _claim(db_session, "k3", body)
    assert first.kind is IdempotencyOutcomeKind.NEW

    second = await _claim(db_session, "k3", body)
    assert second.kind is IdempotencyOutcomeKind.IN_FLIGHT


async def test_in_progress_with_stale_lock_signals_recovery(
    db_session: AsyncSession,
) -> None:
    body = {"amount": 100, "currency": "USD"}
    first = await _claim(db_session, "k4", body)
    assert first.kind is IdempotencyOutcomeKind.NEW

    future = datetime.now(UTC) + timedelta(minutes=5)
    stale = await claim_or_replay(
        db_session,
        key="k4",
        scope="charge:cust_1",
        request_fingerprint=fingerprint(body),
        stale_after=timedelta(seconds=30),
        ttl=timedelta(hours=24),
        now=future,
    )
    assert stale.kind is IdempotencyOutcomeKind.STALE


async def test_expired_key_treated_as_new(db_session: AsyncSession) -> None:
    body = {"amount": 100, "currency": "USD"}
    first = await _claim(db_session, "k5", body)
    assert first.kind is IdempotencyOutcomeKind.NEW

    future = datetime.now(UTC) + timedelta(hours=48)
    out = await claim_or_replay(
        db_session,
        key="k5",
        scope="charge:cust_1",
        request_fingerprint=fingerprint(body),
        stale_after=timedelta(seconds=30),
        ttl=timedelta(hours=24),
        now=future,
    )
    assert out.kind is IdempotencyOutcomeKind.NEW
