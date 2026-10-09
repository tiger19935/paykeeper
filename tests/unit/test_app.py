from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from paykeeper.api.app import create_app
from paykeeper.config import Settings


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("PAYKEEPER_WEBHOOK_SECRET", "unit-test")
    settings = Settings()  # type: ignore[call-arg]

    app = create_app(settings=settings)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=None)
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    sm = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    sm.return_value = cm
    app.state.sessionmaker = sm
    return app


async def test_healthz_ok(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert "X-Request-ID" in resp.headers


async def test_readyz_ok(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


async def test_openapi_served(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "paykeeper"
