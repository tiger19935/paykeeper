from __future__ import annotations

from paykeeper.providers.circuit import CircuitBreaker, CircuitState

_NS = 1_000_000_000


class _Clock:
    def __init__(self) -> None:
        self.t = 0

    def __call__(self) -> int:
        return self.t

    def advance_seconds(self, seconds: int) -> None:
        self.t += seconds * _NS


def _make(**overrides) -> tuple[CircuitBreaker, _Clock]:  # type: ignore[no-untyped-def]
    clock = _Clock()
    cfg = dict(failure_threshold=3, window_seconds=60, open_seconds=30, clock=clock)
    cfg.update(overrides)
    return CircuitBreaker(**cfg), clock


def test_closed_initially() -> None:
    cb, _ = _make()
    assert cb.state is CircuitState.CLOSED
    assert cb.allow() is True


def test_opens_at_threshold() -> None:
    cb, _ = _make()
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert cb.allow() is False


def test_failures_outside_window_do_not_count() -> None:
    cb, clock = _make(window_seconds=10)
    cb.record_failure()
    cb.record_failure()
    clock.advance_seconds(20)
    cb.record_failure()  # first two fell outside window
    assert cb.state is CircuitState.CLOSED


def test_half_open_after_cooldown_admits_one_probe() -> None:
    cb, clock = _make()
    for _ in range(3):
        cb.record_failure()
    assert cb.state is CircuitState.OPEN

    clock.advance_seconds(30)
    assert cb.allow() is True
    assert cb.state is CircuitState.HALF_OPEN
    # Second concurrent caller is denied.
    assert cb.allow() is False


def test_half_open_success_closes() -> None:
    cb, clock = _make()
    for _ in range(3):
        cb.record_failure()
    clock.advance_seconds(30)
    cb.allow()  # take the probe
    cb.record_success()
    assert cb.state is CircuitState.CLOSED
    assert cb.allow() is True


def test_half_open_failure_reopens() -> None:
    cb, clock = _make()
    for _ in range(3):
        cb.record_failure()
    clock.advance_seconds(30)
    cb.allow()
    cb.record_failure()
    assert cb.state is CircuitState.OPEN

    clock.advance_seconds(10)
    assert cb.allow() is False  # still cooling

    clock.advance_seconds(30)
    assert cb.allow() is True
