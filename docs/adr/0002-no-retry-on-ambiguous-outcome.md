# ADR 0002: No retry on ambiguous outcomes

## Status

Accepted — 2026-10-09.

## Context

Three provider errors have very different retry semantics:

- `NetworkError` (connection reset mid-request): the request never reached the
  provider or was discarded. Safe to retry.
- `ProviderUnavailable` (5xx, 429): server-side error after routing. The
  provider's own idempotency guarantees a retry with the same key is safe.
- `ProviderTimeout` (no response within our deadline): **the outcome is
  unknown**. The charge may have succeeded on the provider side; retrying
  blindly risks double-charging if the first attempt did commit.

The literature calls the third case the "ambiguous outcome" problem. The
tempting shortcut — "the provider has idempotency, retry anyway" — only
works if we pass the same idempotency key on retry AND the provider's
dedup window outlives our timeout. Both assumptions fail in practice
(short windows, misrouted retries, replicated backends).

## Decision

- `ProviderTimeout` is **not** retryable (`retryable = False`). It surfaces to
  the handler, which leaves the idempotency key `in_progress` and returns 502
  to the client.
- On the next replay (same key), the handler sees the stale `in_progress`
  row and enters recovery: `provider.get_by_operation_id(operation_id)` to
  learn the actual outcome, then backfill local state.
- Retries for `NetworkError` and `ProviderUnavailable` use exponential
  backoff with full jitter (`providers/backoff.py`).

## Consequences

- A truly ambiguous charge takes an extra round-trip (the provider lookup)
  on retry, but we never charge twice.
- Recovery depends on the provider supporting lookup by our operation id
  (metadata search for Stripe; direct map for the FakeProvider).
- Timeouts therefore require operational attention: they are the one
  error class where a human — or an automated reconciliation pass —
  may be the one to resolve the ambiguity.
