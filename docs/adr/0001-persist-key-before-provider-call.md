# ADR 0001: Persist the idempotency key before the provider call

## Status

Accepted — 2026-10-09.

## Context

A charge handler faces two external systems: Postgres (ours) and the payment
provider (theirs). The outcome of any attempt depends on which side committed
when the process died — a crash between provider success and database write is
the hardest case.

Two orderings are plausible:

1. **Provider first, then persist.** Fast path, one network round-trip. On
   crash after the provider accepted the charge, we have no local record and
   the retrying client sees "no row" and tries again — the card is charged
   twice.
2. **Persist a key row first, then call the provider.** The row commits in a
   short local transaction, state=`in_progress`. If the process dies, the next
   attempt sees that row and routes to recovery: ask the provider "did
   operation `op-X` go through?" rather than retry blindly.

## Decision

Option 2. The handler flow is:

```
BEGIN; INSERT INTO idempotency_keys (…) ON CONFLICT DO NOTHING RETURNING state; COMMIT;
<provider call, keyed by our operation_id>
BEGIN; insert charge + ledger + outbox; UPDATE idempotency_keys SET state='completed', response_body=…; COMMIT;
```

The key scope is `(customer_id, route)` — two unrelated endpoints with the
same key for the same customer must not collide, so we extend the "scope =
customer_id" rule from the brief to include the route (`charge` vs `refund`).

## Consequences

- A crash between the two transactions leaves `state='in_progress'` with a
  fresh `locked_at`. The next attempt sees that row and either:
  (a) returns 409 in-flight while the lock is fresh, or
  (b) enters recovery once the lock is older than `IDEMPOTENCY_LOCK_STALE_SECONDS`.
- We incur one extra DB round-trip per request (the key insert) in exchange
  for a correctness guarantee under crash.
- `operation_id` is a deterministic `uuid5(key, scope)` so provider lookups
  on retry hit the same provider record.
