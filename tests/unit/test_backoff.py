from __future__ import annotations

import random

import pytest

from paykeeper.providers.backoff import compute_delays_ms, with_retries
from paykeeper.providers.base import (
    CardDeclinedError,
    NetworkError,
    ProviderUnavailableError,
)


def test_delays_shape_full_jitter_bounded() -> None:
    rng = random.Random(42)
    delays = compute_delays_ms(attempts=5, base_ms=100, cap_ms=5_000, rng=rng)
    assert len(delays) == 4
    # Exponential upper bounds: 100, 200, 400, 800
    assert delays[0] <= 100
    assert delays[1] <= 200
    assert delays[2] <= 400
    assert delays[3] <= 800
    assert all(d >= 0 for d in delays)


def test_delays_are_deterministic_with_seeded_rng() -> None:
    a = compute_delays_ms(attempts=6, base_ms=50, cap_ms=2_000, rng=random.Random(1))
    b = compute_delays_ms(attempts=6, base_ms=50, cap_ms=2_000, rng=random.Random(1))
    assert a == b


def test_delays_respect_cap() -> None:
    rng = random.Random(0)
    delays = compute_delays_ms(attempts=10, base_ms=100, cap_ms=200, rng=rng)
    assert all(d <= 200 for d in delays)


async def test_with_retries_retries_until_success() -> None:
    sleeps: list[int] = []

    attempts = {"n": 0}

    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise NetworkError("flaky")
        return "ok"

    async def fake_sleep(ms: int) -> None:
        sleeps.append(ms)

    result = await with_retries(
        flaky,
        max_attempts=5,
        base_delay_ms=10,
        max_delay_ms=100,
        rng=random.Random(0),
        sleep=fake_sleep,
    )
    assert result == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2


async def test_with_retries_does_not_retry_non_retryable() -> None:
    async def declines() -> str:
        raise CardDeclinedError("declined")

    async def no_sleep(_: int) -> None:
        raise AssertionError("should not sleep")

    with pytest.raises(CardDeclinedError):
        await with_retries(
            declines,
            max_attempts=5,
            base_delay_ms=1,
            max_delay_ms=10,
            rng=random.Random(0),
            sleep=no_sleep,
        )


async def test_with_retries_raises_last_on_exhaustion() -> None:
    async def always_unavailable() -> str:
        raise ProviderUnavailableError("x")

    async def noop(_: int) -> None:
        return None

    with pytest.raises(ProviderUnavailableError):
        await with_retries(
            always_unavailable,
            max_attempts=3,
            base_delay_ms=1,
            max_delay_ms=10,
            rng=random.Random(0),
            sleep=noop,
        )
