"""Direct exercise of service.py — covers both process_charge and
process_refund by calling the service functions, not the HTTP route.
This makes coverage visible to pytest-cov (ASGI dispatch can slip past
the tracer in some combinations)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from paykeeper.api.errors import (
    CardDeclinedError,
    ChargeNotFoundError,
    IdempotencyInFlightError,
    IdempotencyMismatchError,
    RefundExceedsBalanceError,
)
from paykeeper.domain.service import process_charge, process_refund
from paykeeper.providers.circuit import CircuitBreaker
from paykeeper.providers.fake import FakeProvider, FakeProviderBehavior
from paykeeper.providers.router import ProviderRouter

pytestmark = pytest.mark.integration


def _router(**overrides) -> ProviderRouter:  # type: ignore[no-untyped-def]
    primary = overrides.pop("primary", FakeProvider(instance="primary"))
    secondary = overrides.pop("secondary", FakeProvider(instance="secondary"))
    return ProviderRouter(
        primary=primary,
        secondary=secondary,
        breaker=CircuitBreaker(failure_threshold=5, window_seconds=60, open_seconds=30),
        retry_max_attempts=1,
        **overrides,
    )


_CHARGE = {
    "amount": 1000,
    "currency": "USD",
    "customer_id": "c-direct",
    "payment_method_token": "pm_card_ok",
    "description": "direct",
}


async def test_process_charge_new_and_replay(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    router = _router()
    async with sessionmaker_() as s:
        status, body = await process_charge(
            s,
            router,
            idempotency_key="k-dir-1",
            body=_CHARGE,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 201
    assert body["status"] == "succeeded"

    async with sessionmaker_() as s:
        status, body = await process_charge(
            s,
            router,
            idempotency_key="k-dir-1",
            body=_CHARGE,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 200


async def test_process_charge_mismatch_and_in_flight(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    router = _router()
    async with sessionmaker_() as s:
        await process_charge(
            s,
            router,
            idempotency_key="k-dir-2",
            body=_CHARGE,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )

    async with sessionmaker_() as s:
        with pytest.raises(IdempotencyMismatchError):
            await process_charge(
                s,
                router,
                idempotency_key="k-dir-2",
                body={**_CHARGE, "amount": 999},
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )


async def test_process_charge_card_declined(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    router = _router()
    async with sessionmaker_() as s:
        with pytest.raises(CardDeclinedError):
            await process_charge(
                s,
                router,
                idempotency_key="k-dir-decline",
                body={**_CHARGE, "payment_method_token": "pm_decline"},
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )
    # Replay returns the stored 402 response.
    async with sessionmaker_() as s:
        status, body = await process_charge(
            s,
            router,
            idempotency_key="k-dir-decline",
            body={**_CHARGE, "payment_method_token": "pm_decline"},
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 402
    assert body["status"] == "failed"


async def test_process_refund_full_flow_including_insufficient_balance(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    router = _router()
    async with sessionmaker_() as s:
        _, charge = await process_charge(
            s,
            router,
            idempotency_key="k-dir-r-ch",
            body=_CHARGE,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )

    async with sessionmaker_() as s:
        status, body = await process_refund(
            s,
            router,
            idempotency_key="k-dir-rf-1",
            body={"charge_id": charge["id"], "amount": 300, "reason": "ok"},
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 201
    assert body["status"] == "succeeded"

    # Replay refund
    async with sessionmaker_() as s:
        status, _ = await process_refund(
            s,
            router,
            idempotency_key="k-dir-rf-1",
            body={"charge_id": charge["id"], "amount": 300, "reason": "ok"},
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 200

    # Exceeds remaining
    async with sessionmaker_() as s:
        with pytest.raises(RefundExceedsBalanceError):
            await process_refund(
                s,
                router,
                idempotency_key="k-dir-rf-big",
                body={"charge_id": charge["id"], "amount": 10_000},
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )


async def test_process_refund_unknown_charge(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    router = _router()
    async with sessionmaker_() as s:
        with pytest.raises(ChargeNotFoundError):
            await process_refund(
                s,
                router,
                idempotency_key="k-dir-rf-none",
                body={"charge_id": "not-a-uuid", "amount": 1},
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )


async def test_process_charge_provider_unavailable_bubbles(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    from paykeeper.api.errors import ProviderUnavailableError as HTTPProviderUnavailable

    primary = FakeProvider(
        instance="primary", behavior=FakeProviderBehavior(always_unavailable=True)
    )
    # No secondary → the primary's error propagates.
    router = ProviderRouter(
        primary=primary,
        secondary=None,
        breaker=CircuitBreaker(failure_threshold=5, window_seconds=60, open_seconds=30),
        retry_max_attempts=1,
    )

    async with sessionmaker_() as s:
        with pytest.raises(HTTPProviderUnavailable):
            await process_charge(
                s,
                router,
                idempotency_key="k-dir-up",
                body=_CHARGE,
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )


async def test_process_charge_in_flight_short_budget(
    sessionmaker_: async_sessionmaker,  # type: ignore[type-arg]
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    """Seed an in_progress key with a fresh lock, then verify the service
    returns IN_FLIGHT after exhausting its poll budget."""

    from datetime import UTC, datetime

    from paykeeper.domain.models import IdempotencyKey
    from paykeeper.idempotency.fingerprint import fingerprint

    router = _router()
    key = "k-dir-inflight"
    body = _CHARGE
    scope = f"charge:{body['customer_id']}"

    async with sessionmaker_() as s:
        s.add(
            IdempotencyKey(
                key=key,
                scope=scope,
                request_fingerprint=fingerprint(body),
                state="in_progress",
                locked_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await s.commit()

    async with sessionmaker_() as s:
        with pytest.raises(IdempotencyInFlightError):
            await process_charge(
                s,
                router,
                idempotency_key=key,
                body=body,
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )
