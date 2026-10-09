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
from paykeeper.domain.models import LedgerEntry, Refund
from paykeeper.providers.circuit import CircuitBreaker
from paykeeper.providers.fake import FakeProvider
from paykeeper.providers.router import ProviderRouter

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app(
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[FastAPI]:
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


_CHARGE_BODY = {
    "amount": 1000,
    "currency": "USD",
    "customer_id": "cust_r",
    "payment_method_token": "pm_card_ok",
    "description": "for refunds",
}


async def _create_charge(client: httpx.AsyncClient, key: str = "k-charge-r") -> dict:
    resp = await client.post("/v1/charges", json=_CHARGE_BODY, headers={"Idempotency-Key": key})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_refund_happy_path(client: httpx.AsyncClient) -> None:
    charge = await _create_charge(client)
    resp = await client.post(
        "/v1/refunds",
        json={"charge_id": charge["id"], "amount": 400, "reason": "customer_request"},
        headers={"Idempotency-Key": "k-ref-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount"] == 400
    assert body["status"] == "succeeded"
    assert body["charge_id"] == charge["id"]


async def test_refund_replay(client: httpx.AsyncClient) -> None:
    charge = await _create_charge(client, key="k-charge-r2")
    body = {"charge_id": charge["id"], "amount": 100}
    first = await client.post("/v1/refunds", json=body, headers={"Idempotency-Key": "k-ref-replay"})
    second = await client.post(
        "/v1/refunds", json=body, headers={"Idempotency-Key": "k-ref-replay"}
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()


async def test_refund_exceeds_balance(client: httpx.AsyncClient) -> None:
    charge = await _create_charge(client, key="k-charge-r3")
    resp = await client.post(
        "/v1/refunds",
        json={"charge_id": charge["id"], "amount": 10_000},
        headers={"Idempotency-Key": "k-ref-big"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "refund_exceeds_balance"


async def test_two_concurrent_refunds_racing_the_balance(
    client: httpx.AsyncClient,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Charge is 1000. Two refunds of 600 fire together — only one wins."""

    charge = await _create_charge(client, key="k-charge-race")
    cid = charge["id"]

    async def refund(key: str) -> httpx.Response:
        return await client.post(
            "/v1/refunds",
            json={"charge_id": cid, "amount": 600},
            headers={"Idempotency-Key": key},
        )

    r1, r2 = await asyncio.gather(refund("k-race-A"), refund("k-race-B"))

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 422], f"unexpected status codes: {codes}"

    async with sessionmaker_() as session:
        succeeded_refunds = (
            await session.execute(
                select(func.count()).select_from(Refund).where(Refund.state == "succeeded")
            )
        ).scalar_one()
        entries = (
            await session.execute(select(func.count()).select_from(LedgerEntry))
        ).scalar_one()
    assert succeeded_refunds == 1
    # One charge ledger entry + one refund ledger entry.
    assert entries == 2


async def test_refund_not_for_unknown_charge(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/refunds",
        json={"charge_id": "00000000-0000-0000-0000-000000000000", "amount": 1},
        headers={"Idempotency-Key": "k-no-charge"},
    )
    assert resp.status_code == 404


async def test_get_refund_by_id(client: httpx.AsyncClient) -> None:
    charge = await _create_charge(client, key="k-charge-r4")
    created = await client.post(
        "/v1/refunds",
        json={"charge_id": charge["id"], "amount": 1},
        headers={"Idempotency-Key": "k-ref-get"},
    )
    rid = created.json()["id"]
    got = await client.get(f"/v1/refunds/{rid}")
    assert got.status_code == 200
    assert got.json()["id"] == rid
