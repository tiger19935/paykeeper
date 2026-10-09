from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from paykeeper.api.app import create_app
from paykeeper.config import Settings
from paykeeper.domain.models import Charge, LedgerEntry
from paykeeper.providers.circuit import CircuitBreaker
from paykeeper.providers.fake import FakeProvider
from paykeeper.providers.router import ProviderRouter

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app(
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[FastAPI]:
    """Create a fresh FastAPI app with an isolated FakeProvider.

    Depending on `db_session` ensures tables are truncated before each test.
    """

    os.environ["PAYKEEPER_WEBHOOK_SECRET"] = "test-secret"
    settings = Settings()  # type: ignore[call-arg]
    app = create_app(settings=settings)
    app.state.sessionmaker = sessionmaker_
    primary = FakeProvider(secret="test-secret", instance="primary")
    secondary = FakeProvider(secret="test-secret", instance="secondary")
    app.state.primary_provider = primary
    app.state.provider_router = ProviderRouter(
        primary=primary,
        secondary=secondary,
        breaker=CircuitBreaker(
            failure_threshold=settings.breaker_failure_threshold,
            window_seconds=settings.breaker_window_seconds,
            open_seconds=settings.breaker_open_seconds,
        ),
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_ms=settings.retry_base_delay_ms,
        retry_max_delay_ms=settings.retry_max_delay_ms,
    )
    yield app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


_BODY = {
    "amount": 1000,
    "currency": "USD",
    "customer_id": "cust_1",
    "payment_method_token": "pm_card_ok",
    "description": "test charge",
}


async def test_charge_created(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/charges",
        json=_BODY,
        headers={"Idempotency-Key": "k-new-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["amount"] == 1000
    assert body["provider_ref"].startswith("ch_primary_")


async def test_replay_returns_identical_response(client: httpx.AsyncClient) -> None:
    first = await client.post("/v1/charges", json=_BODY, headers={"Idempotency-Key": "k-replay"})
    assert first.status_code == 201
    second = await client.post("/v1/charges", json=_BODY, headers={"Idempotency-Key": "k-replay"})
    assert second.status_code == 201
    assert second.json() == first.json()


async def test_same_key_different_body_409(client: httpx.AsyncClient) -> None:
    first = await client.post("/v1/charges", json=_BODY, headers={"Idempotency-Key": "k-mismatch"})
    assert first.status_code == 201
    other = dict(_BODY) | {"amount": 999}
    second = await client.post("/v1/charges", json=other, headers={"Idempotency-Key": "k-mismatch"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_reused"


async def test_card_declined_persists_as_failed_and_replays(
    client: httpx.AsyncClient,
) -> None:
    body = dict(_BODY) | {"payment_method_token": "pm_decline"}
    resp = await client.post("/v1/charges", json=body, headers={"Idempotency-Key": "k-decline"})
    assert resp.status_code == 402
    # Replay: same stored status/body
    replay = await client.post("/v1/charges", json=body, headers={"Idempotency-Key": "k-decline"})
    assert replay.status_code == 402


async def test_fifty_concurrent_identical_requests(
    client: httpx.AsyncClient,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    key = "k-concurrent-50"

    async def one() -> httpx.Response:
        return await client.post(
            "/v1/charges",
            json=_BODY,
            headers={"Idempotency-Key": key},
        )

    responses = await asyncio.gather(*(one() for _ in range(50)))

    for r in responses:
        assert r.status_code == 201, r.text

    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 1

    # Exactly one provider call → one charge row + one ledger entry.
    async with sessionmaker_() as session:
        charges = (await session.execute(select(func.count()).select_from(Charge))).scalar_one()
        entries = (
            await session.execute(select(func.count()).select_from(LedgerEntry))
        ).scalar_one()
    assert charges == 1
    assert entries == 1


async def test_get_charge_by_id(client: httpx.AsyncClient) -> None:
    created = await client.post("/v1/charges", json=_BODY, headers={"Idempotency-Key": "k-get"})
    assert created.status_code == 201
    cid = created.json()["id"]
    got = await client.get(f"/v1/charges/{cid}")
    assert got.status_code == 200
    assert got.json()["id"] == cid


async def test_get_charge_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/charges/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
