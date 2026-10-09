# ADR 0004: Idempotency key scope includes the route

## Status

Accepted — 2026-10-09.

## Context

The brief specifies "scope = customer_id" so two customers who happen to use
the same client-supplied key cannot collide. The question is whether the
*same* customer sending the same key to two different endpoints should also
collide.

In practice, clients regenerate idempotency keys per request, but auto-retrying
HTTP clients sometimes reuse them across unrelated API calls that failed and
got re-dispatched. A `POST /v1/charges` and a `POST /v1/refunds` for the same
customer sharing a key is not a replay — they are different operations.

## Decision

Scope is `(customer_id, route)` — for charges, `charge:<customer_id>`; for
refunds, `refund:<charge_id>`. (Refunds scope by charge_id because the
request body carries the charge id but not the customer id; the charge id
embeds the ownership relationship.)

The `operation_id` we forward to the provider is derived from
`uuid5(fixed-namespace, scope + ":" + key)`, so collisions are impossible
by construction.

## Consequences

- Clients can safely reuse keys across endpoints without hitting a false
  409 mismatch.
- A client that *wants* a charge and refund to be the same logical idempotent
  operation (which they aren't) can still assemble that themselves at the
  application layer; we don't impede it, we just don't encode it.
