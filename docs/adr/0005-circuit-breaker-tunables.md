# ADR 0005: Circuit breaker tunables are demo-grade

## Status

Accepted — 2026-10-09.

## Context

The circuit breaker's defaults (`failure_threshold=5` within `window=60s`,
open for `30s`) were chosen so tests can observe the transition
CLOSED → OPEN → HALF_OPEN → CLOSED quickly, with a seedable monotonic
clock. In production, these numbers are not right: a 5-failure threshold
opens the breaker from a single slow deployment on a healthy provider.

## Decision

Expose the three tunables as settings (`PAYKEEPER_BREAKER_FAILURE_THRESHOLD`,
`_WINDOW_SECONDS`, `_OPEN_SECONDS`) and keep the defaults low enough that
`tests/integration/test_failover.py` finishes in milliseconds. Document in
this ADR that production deployments must retune.

## Consequences

- The failover test is fast and deterministic.
- Operators cannot adopt the defaults verbatim without reading. The README
  and the ADR both say so.
- A richer policy — sliding-window error-rate, latency-based opening — is
  deliberately out of scope. The breaker here is a reference implementation
  of the state machine, not a production decision engine.
