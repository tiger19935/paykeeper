# ADR 0003: Single-entry append-only ledger

## Status

Accepted — 2026-10-09.

## Context

Double-entry bookkeeping (debit/credit, journal of balanced transactions
across accounts) is the right model for a company's internal money
movements — AP/AR, settlement, custody. Our service's job is narrower:
record what the provider did for us so reconciliation can detect drift.

Picking double-entry here would mean inventing accounts (customer, provider,
house) and modelling settlement timing we don't own. It would also make the
smallest audit — "did we charge this customer, and when?" — a multi-row join.

## Decision

A single table, `ledger_entries`, append-only:

- One row per authoritative state change (`charge.succeeded`,
  `refund.succeeded`).
- Columns: `entry_type`, `amount`, `currency`, `charge_id`, `refund_id?`,
  `provider`, `provider_ref`, `created_at`.
- A PostgreSQL `BEFORE UPDATE OR DELETE` trigger raises
  `ledger_entries is append-only` on any mutation attempt. The trigger is
  tested, not just hoped for.

## Consequences

- Reconciliation becomes "compare `ledger_entries` with the provider's
  record for the window." Simple.
- Appending (vs. updating) means webhook state changes are modelled on the
  `charges`/`refunds` rows, not on ledger entries. The ledger records the
  moment of money movement; the state machine lives alongside, not inside.
- If the service ever needs double-entry (e.g., we start settling fees,
  taking fees, or holding balances), this ledger stays as the "external
  provider mirror" and a separate internal journal is added above it.
