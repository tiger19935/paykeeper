# Failure modes

Every row is one thing that can go wrong, how the system behaves, and
which test proves it. Keep this table in sync with the code.

| # | Scenario | Behavior | Proof |
|---|---|---|---|
| 1 | Duplicate `Idempotency-Key`, same body, after completion | 200 on replay, identical response body | `tests/integration/test_charges.py::test_replay_returns_identical_response` |
| 2 | Duplicate `Idempotency-Key`, different body | 409 `idempotency_key_reused` | `test_same_key_different_body_409` |
| 3 | 50 concurrent identical requests | Exactly one provider call, one ledger entry, one charge id returned 50× | `test_fifty_concurrent_identical_requests` |
| 4 | Card declined | 402, persisted as `state='failed'`, replay returns same 402 | `test_card_declined_persists_as_failed_and_replays` |
| 5 | Two refunds racing for the same remaining balance | Exactly one succeeds (`SELECT … FOR UPDATE` + read-committed) | `tests/integration/test_refunds.py::test_two_concurrent_refunds_racing_the_balance` |
| 6 | Provider timeout with the charge actually accepted | First call → 502, next retry (after stale lock) enters recovery, finds the provider's record, backfills — one charge, one ledger entry | `tests/integration/test_recovery.py::test_ambiguous_outcome_recovers_to_single_charge` |
| 7 | Worker died before the provider accepted anything | Stale lock → recovery finds nothing at provider → deletes stale key → re-runs the charge | `test_stale_lock_with_no_provider_record_falls_through_to_fresh_charge` |
| 8 | Primary provider returning errors | Breaker opens after N failures, charges route to secondary; after cooldown a half-open probe tries primary again | `tests/integration/test_failover.py::test_primary_down_opens_breaker_and_secondary_takes_over` |
| 9 | Webhook delivered twice | First application mutates state, second is deduped by `UNIQUE(provider, provider_event_id)` and ack'd 200 | `tests/integration/test_webhooks.py::test_charge_succeeded_webhook_applies_once` |
| 10 | Tampered webhook signature | 400 `invalid_webhook`, no DB write | `test_bad_signature_rejected` |
| 11 | Replayed webhook with an old timestamp | 400 (outside tolerance window) | `test_replayed_timestamp_rejected` |
| 12 | Attempt to UPDATE or DELETE a ledger row | Postgres trigger raises; transaction aborts | `tests/integration/test_ledger_trigger.py` |
| 13 | Outbox drain run twice at once | `pg_try_advisory_xact_lock` denies the second runner; it exits 0 having published nothing | `tests/integration/test_outbox_drain.py` |
| 14 | Reconcile with injected mismatch | Non-zero exit; mismatch kinds enumerated: `missing_local`, `missing_remote`, `amount_mismatch`, `state_mismatch` | `tests/integration/test_reconcile.py` |
| 15 | Float amount in a request body | Pydantic `StrictInt` rejects at the HTTP boundary; AST test rejects `float` anywhere in money paths | `tests/unit/test_no_float_in_money_paths.py` + the API schema |
