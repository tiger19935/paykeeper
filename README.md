# paykeeper

[![CI](https://github.com/tiger19935/paykeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/tiger19935/paykeeper/actions/workflows/ci.yml)

An idempotent charge/refund HTTP service with provider failover,
append-only ledger, webhook ingestion, and a reconciliation CLI. Written
as a reference implementation of patterns I ran in production
(PHP/Laravel at a US fintech; Python on AWS Lambda), rewritten here as a
standalone Python 3.12 service on FastAPI + SQLAlchemy 2.0 + PostgreSQL.

The repository is small on purpose. Every file exists for a reason, and
every documented behaviour has a test that fails if it regresses.

## Quick start

Requires Docker and `curl`.

```sh
docker compose up -d --build       # postgres + migrator + app
curl http://localhost:58000/readyz # {"status":"ready"}
```

The quick-start compose file maps Postgres to host port **55432** and the
API to host port **58000**, so paykeeper doesn't collide with any
Postgres you already run on 5432.

Call the API:

```sh
BASE=http://localhost:58000

# 1. Create a charge (201)
curl -sS -w "\nHTTP %{http_code}\n" -X POST $BASE/v1/charges \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-k-1' \
  -d '{"amount":1200,"currency":"USD","customer_id":"cust_demo","payment_method_token":"pm_card_ok","description":"first"}'

# 2. Replay same key, same body (200, identical response)
curl -sS -w "\nHTTP %{http_code}\n" -X POST $BASE/v1/charges \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-k-1' \
  -d '{"amount":1200,"currency":"USD","customer_id":"cust_demo","payment_method_token":"pm_card_ok","description":"first"}'

# 3. Same key, different body (409 idempotency_key_reused)
curl -sS -w "\nHTTP %{http_code}\n" -X POST $BASE/v1/charges \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-k-1' \
  -d '{"amount":999,"currency":"USD","customer_id":"cust_demo","payment_method_token":"pm_card_ok","description":"different"}'

# Refund (needs the charge id from step 1)
CHARGE_ID=$(curl -sS -X POST $BASE/v1/charges \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-ch-r' \
  -d '{"amount":500,"currency":"USD","customer_id":"c","payment_method_token":"pm_card_ok"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -sS -X POST $BASE/v1/refunds \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-rf-1' \
  -d "{\"charge_id\":\"$CHARGE_ID\",\"amount\":200,\"reason\":\"customer_request\"}"
```

OpenAPI docs: <http://localhost:58000/docs>.

## API overview

| Method | Path | Headers | Status codes |
|---|---|---|---|
| POST | `/v1/charges` | `Idempotency-Key` required | 201 created, 200 replay, 409 mismatch, 409 in-flight, 402 card_declined, 502 provider_unavailable |
| POST | `/v1/refunds` | `Idempotency-Key` required | 201 created, 200 replay, 409, 422 refund_exceeds_balance, 404 charge_not_found |
| GET | `/v1/charges/{id}` | — | 200, 404 |
| GET | `/v1/refunds/{id}` | — | 200, 404 |
| POST | `/v1/webhooks/{provider}` | `X-Signature`, `X-Signature-Timestamp` | 200, 400 invalid_webhook |
| GET | `/healthz`, `/readyz` | — | 200 ready, 503 not_ready |

## How idempotency works

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant S as paykeeper
    participant DB as Postgres
    participant P as Provider
    C->>S: POST /v1/charges + Idempotency-Key
    S->>DB: INSERT INTO idempotency_keys ... ON CONFLICT DO NOTHING
    alt inserted (NEW)
        S->>P: charge(..., operation_id=uuid5(scope,key))
        P-->>S: provider_ref
        S->>DB: BEGIN; insert charge + ledger + outbox; UPDATE key SET state='completed'; COMMIT
        S-->>C: 201 { charge object }
    else conflict, fingerprint matches, state=completed (REPLAY)
        S-->>C: 200 { same charge object }
    else conflict, fingerprint matches, state=in_progress, fresh (IN_FLIGHT)
        S-->>C: 409 idempotency_in_flight
    else conflict, fingerprint matches, state=in_progress, stale (STALE)
        S->>P: get_by_operation_id
        P-->>S: prior charge or none
        S->>DB: backfill, mark completed
        S-->>C: 201 or 502 as applicable
    else conflict, fingerprint differs (MISMATCH)
        S-->>C: 409 idempotency_key_reused
    end
```

The key row is persisted **before** the provider call so a crash between
provider success and local write can be recovered rather than paid for
again. See [`docs/adr/0001-persist-key-before-provider-call.md`](docs/adr/0001-persist-key-before-provider-call.md).

## Failure modes

Every documented failure mode has a corresponding integration test. See
[`docs/failure-modes.md`](docs/failure-modes.md) for the full table.

## Load

Measured locally on an Apple M-class machine via Docker Desktop
(not a benchmark; a sanity check that the idempotency path doesn't
accidentally serialize):

```
k6 run --duration 20s --vus 30 load/charges.js   # 30 concurrent workers
http_reqs .........................: 5225   260 req/s
http_req_duration p(95) ...........: 131 ms
http_req_duration p(90) ...........: 119 ms
http_req_failed rate ..............: 0.00% (0 / 5225)
checks succeeded .................: 99.83% (status 200 on the 17 replayed hits)
```

Post-run invariant check (`load/duplicate_check.sql`) returns zero rows
in all three queries: no duplicate charge rows per `operation_id`, no
duplicate ledger entries per charge, no idempotency key pointing at more
than one charge.

## Local development

```sh
uv sync
make lint      # ruff check + format check
make typecheck # mypy --strict
make test      # pytest with testcontainers-postgres, coverage ≥ 85%, 100% on critical modules
```

Integration tests spin up Postgres via testcontainers; `make test`
auto-skips integration tests if Docker is not running.

## Reconciliation

```sh
docker compose exec -T postgres psql -U paykeeper -d paykeeper < /dev/null  # ensure healthy
PAYKEEPER_DATABASE_URL=postgresql+asyncpg://paykeeper:paykeeper@localhost:55432/paykeeper \
PAYKEEPER_WEBHOOK_SECRET=dev-secret-change-me \
  uv run paykeeper reconcile --since 24h
```

Exits 0 if the local ledger agrees with the provider; non-zero if any
`missing_local`, `missing_remote`, `amount_mismatch`, or `state_mismatch`
is found. Safe to re-run.

## Deliberately out of scope

- A real message bus for outbox delivery (`paykeeper outbox-drain`
  writes to stdout; swap for Kafka/SNS by replacing one function).
- Hitting real Stripe from CI (contract tests use `respx`; a developer
  with a Stripe test key can set `PAYKEEPER_PRIMARY_PROVIDER=stripe` locally).
- Multi-currency FX, settlement, payouts, chargebacks.
- AuthN/AuthZ on the API — this is a demo service, not a tenant-isolated SaaS.
- Horizontal scaling of the reconciliation job; the Postgres advisory
  lock makes it single-process.

## License

MIT. Author: Andrii Boiko (<a.boyko.93@gmail.com>).
