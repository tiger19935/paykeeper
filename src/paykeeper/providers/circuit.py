"""Circuit breaker.

States: CLOSED → OPEN → HALF_OPEN → CLOSED (on probe success) or back
to OPEN (on probe failure).

Transitions:
- CLOSED  : record_success() clears the failure window; record_failure()
            adds a timestamp. When failures within `window_seconds` reach
            `failure_threshold`, go to OPEN.
- OPEN    : allow() returns False until `open_seconds` has elapsed, then
            transitions to HALF_OPEN and admits one probe.
- HALF_OPEN: the probe either succeeds (→ CLOSED, failures cleared) or
             fails (→ OPEN, timer resets).

The clock is injectable so tests can advance time without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_NS_PER_S = 1_000_000_000


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        window_seconds: int,
        open_seconds: int,
        clock: Callable[[], int] | None = None,
    ) -> None:
        """Timestamps are integer nanoseconds (`time.monotonic_ns`).

        No `float` is used anywhere in this module: the providers package
        is checked by `tests/unit/test_no_float_in_money_paths.py`, and
        money-adjacent code must stay float-free end to end.
        """

        self.failure_threshold = failure_threshold
        self.window_ns = window_seconds * _NS_PER_S
        self.open_ns = open_seconds * _NS_PER_S
        self._clock = clock or time.monotonic_ns
        self._state: CircuitState = CircuitState.CLOSED
        self._failures: list[int] = []
        self._opened_at: int | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        now = self._clock()
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and now - self._opened_at >= self.open_ns
        ):
            self._state = CircuitState.HALF_OPEN
            self._probe_in_flight = False
        return self._state

    def allow(self) -> bool:
        state = self.state
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.OPEN:
            return False
        # HALF_OPEN: admit one probe.
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        now = self._clock()
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = now
            self._probe_in_flight = False
            return

        self._failures.append(now)
        cutoff = now - self.window_ns
        self._failures = [t for t in self._failures if t >= cutoff]
        if len(self._failures) >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = now
