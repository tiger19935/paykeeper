from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from paykeeper.api.app import create_app
from paykeeper.config import Settings
from paykeeper.domain.models import Charge, WebhookEvent
from paykeeper.providers.circuit import CircuitBreaker
from paykeeper.providers.fake import FakeProvider
from paykeeper.providers.router import ProviderRouter

pytestmark = pytest.mark.integration

_SECRET = "wh-test-secret"


@pytest_asyncio.fixture
async def app(
    db_session: AsyncSession,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[FastAPI]:
    os.environ["PAYKEEPER_WEBHOOK_SECRET"] = _SECRET
    settings = Settings()  # type: ignore[call-arg]
    app = create_app(settings=settings)
    app.state.sessionmaker = sessionmaker_
    primary = FakeProvider(secret=_SECRET, instance="primary")
    secondary = FakeProvider(secret=_SECRET, instance="secondary")
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


def _sign(body: bytes, ts: int) -> str:
    return hmac.new(_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


async def _seed_charge(
    sessionmaker_: async_sessionmaker[AsyncSession],
    provider_ref: str,
) -> uuid.UUID:
    cid = uuid.uuid4()
    async with sessionmaker_() as session:
        session.add(
            Charge(
                id=cid,
                customer_id="c1",
                amount=100,
                currency="USD",
                payment_method_token="pm_card",
                state="pending",
                provider="fake",
                provider_ref=provider_ref,
                operation_id=f"op-{cid}",
            )
        )
        await session.commit()
    return cid


async def test_charge_succeeded_webhook_applies_once(
    client: httpx.AsyncClient,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _seed_charge(sessionmaker_, provider_ref="ch_whk_1")

    payload = {
        "id": "evt_wh_1",
        "type": "charge.succeeded",
        "data": {"provider_ref": "ch_whk_1"},
    }
    body = json.dumps(payload).encode()
    ts = int(time.time())
    headers = {"X-Signature": _sign(body, ts), "X-Signature-Timestamp": str(ts)}

    first = await client.post(
        "/v1/webhooks/fake", content=body, headers=headers | {"Content-Type": "application/json"}
    )
    assert first.status_code == 200
    assert first.json()["applied"] is True

    second = await client.post(
        "/v1/webhooks/fake", content=body, headers=headers | {"Content-Type": "application/json"}
    )
    assert second.status_code == 200
    assert second.json()["applied"] is False

    async with sessionmaker_() as session:
        charge = (await session.execute(select(Charge).where(Charge.id == cid))).scalar_one()
        count = (await session.execute(select(func.count()).select_from(WebhookEvent))).scalar_one()
    assert charge.state == "succeeded"
    assert count == 1


async def test_bad_signature_rejected(client: httpx.AsyncClient) -> None:
    body = b'{"id":"evt_x","type":"charge.succeeded"}'
    ts = int(time.time())
    headers = {
        "X-Signature": "deadbeef",
        "X-Signature-Timestamp": str(ts),
        "Content-Type": "application/json",
    }
    resp = await client.post("/v1/webhooks/fake", content=body, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_webhook"


async def test_replayed_timestamp_rejected(client: httpx.AsyncClient) -> None:
    body = b'{"id":"evt_r","type":"charge.succeeded"}'
    ts = int(time.time()) - 3600
    headers = {
        "X-Signature": _sign(body, ts),
        "X-Signature-Timestamp": str(ts),
        "Content-Type": "application/json",
    }
    resp = await client.post("/v1/webhooks/fake", content=body, headers=headers)
    assert resp.status_code == 400


async def test_unknown_event_type_stored_and_acked(
    client: httpx.AsyncClient,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    body = json.dumps({"id": "evt_u1", "type": "something.unknown"}).encode()
    ts = int(time.time())
    headers = {
        "X-Signature": _sign(body, ts),
        "X-Signature-Timestamp": str(ts),
        "Content-Type": "application/json",
    }
    resp = await client.post("/v1/webhooks/fake", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    async with sessionmaker_() as session:
        count = (await session.execute(select(func.count()).select_from(WebhookEvent))).scalar_one()
    assert count == 1
