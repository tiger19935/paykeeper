from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from paykeeper.domain.models import Charge
from paykeeper.domain.service import process_charge
from paykeeper.providers.circuit import CircuitBreaker, CircuitState
from paykeeper.providers.fake import FakeProvider, FakeProviderBehavior
from paykeeper.providers.router import ProviderRouter

pytestmark = pytest.mark.integration


class _Clock:
    def __init__(self) -> None:
        self.t_ns = 0

    def __call__(self) -> int:
        return self.t_ns

    def advance_s(self, seconds: int) -> None:
        self.t_ns += seconds * 1_000_000_000


_BODY = {
    "amount": 100,
    "currency": "USD",
    "customer_id": "cust_f",
    "payment_method_token": "pm_card",
    "description": "failover",
}


async def _charge(
    sm: async_sessionmaker[AsyncSession],
    router: ProviderRouter,
    key: str,
) -> dict:
    from datetime import timedelta

    async with sm() as session:
        status, body = await process_charge(
            session,
            router,
            idempotency_key=key,
            body={**_BODY, "customer_id": f"c_{key}"},
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 201, body
    return body


async def test_primary_down_opens_breaker_and_secondary_takes_over(
    sessionmaker_: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
) -> None:
    clock = _Clock()
    primary = FakeProvider(
        instance="primary",
        behavior=FakeProviderBehavior(always_unavailable=True),
    )
    secondary = FakeProvider(instance="secondary")

    breaker = CircuitBreaker(
        failure_threshold=2,
        window_seconds=60,
        open_seconds=30,
        clock=clock,
    )
    router = ProviderRouter(
        primary=primary,
        secondary=secondary,
        breaker=breaker,
        retry_max_attempts=1,  # one shot per attempt — opens breaker fast
    )

    # Each call fails on primary (counts against breaker), then succeeds on secondary.
    body1 = await _charge(sessionmaker_, router, key="k-f-1")
    body2 = await _charge(sessionmaker_, router, key="k-f-2")
    assert body1["provider"] == "fake:secondary"
    assert body2["provider"] == "fake:secondary"
    assert breaker.state is CircuitState.OPEN

    # While OPEN, the primary is skipped entirely — no attempt counts.
    body3 = await _charge(sessionmaker_, router, key="k-f-3")
    assert body3["provider"] == "fake:secondary"
    assert breaker.state is CircuitState.OPEN

    # Advance past open_seconds; next attempt is a half-open probe on primary.
    # Primary is still unavailable → probe fails → back to OPEN.
    clock.advance_s(31)
    body4 = await _charge(sessionmaker_, router, key="k-f-4")
    assert body4["provider"] == "fake:secondary"

    # Fix primary. Advance past cooldown. Next attempt probes primary (succeeds).
    primary.behavior.always_unavailable = False
    clock.advance_s(31)
    body5 = await _charge(sessionmaker_, router, key="k-f-5")
    assert body5["provider"] == "fake:primary"
    assert breaker.state is CircuitState.CLOSED

    # Confirm persistence: all five rows exist, with correct providers.
    async with sessionmaker_() as session:
        rows = (await session.execute(select(Charge).order_by(Charge.created_at))).scalars().all()
    assert [r.provider for r in rows] == [
        "fake:secondary",
        "fake:secondary",
        "fake:secondary",
        "fake:secondary",
        "fake:primary",
    ]
