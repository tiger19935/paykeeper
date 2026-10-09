"""Exponential backoff with full jitter.

Delay schedule: `delay_i = min(cap, base * 2 ** (i - 1))` where `i` is the
1-indexed attempt about to be slept-before. "Full jitter" draws the actual
sleep from `[0, delay_i]` uniformly; this smooths retry stampedes on a
briefly-unavailable provider. See AWS Architecture Blog, "Exponential
Backoff And Jitter" (2015).

The function is deterministic given a seeded `random.Random` and a
monotonic `sleep` callable, so tests do not need to run real time.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from paykeeper.providers.base import ProviderError


def compute_delays_ms(
    *,
    attempts: int,
    base_ms: int,
    cap_ms: int,
    rng: random.Random,
) -> list[int]:
    """Return the per-attempt sleep in milliseconds for all retries.

    Length is `attempts - 1` because the first call is immediate.
    """

    out: list[int] = []
    for i in range(1, attempts):
        exp_delay = min(cap_ms, base_ms * (2 ** (i - 1)))
        out.append(rng.randint(0, exp_delay))
    return out


async def with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_ms: int,
    max_delay_ms: int,
    rng: random.Random | None = None,
    sleep: Callable[[int], Awaitable[None]] | None = None,
) -> T:
    """Retry `operation` on retryable `ProviderError`s.

    Non-retryable errors propagate immediately. On exhaustion, the last
    error is re-raised unchanged.
    """

    rng = rng or random.Random()  # noqa: S311
    delays = compute_delays_ms(
        attempts=max_attempts,
        base_ms=base_delay_ms,
        cap_ms=max_delay_ms,
        rng=rng,
    )
    _sleep: Callable[[int], Awaitable[None]] = sleep or _default_sleep_ms

    for attempt in range(max_attempts):
        try:
            return await operation()
        except ProviderError as exc:
            is_last = attempt == max_attempts - 1
            if is_last or not exc.retryable:
                raise
            await _sleep(delays[attempt])
    raise RuntimeError("unreachable")  # pragma: no cover


async def _default_sleep_ms(ms: int) -> None:
    await asyncio.sleep(ms / 1000)
