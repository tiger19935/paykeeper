from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from paykeeper.api.errors import install_error_handlers
from paykeeper.api.routes import charges, health, refunds
from paykeeper.config import Settings, get_settings
from paykeeper.db import create_engine, create_sessionmaker
from paykeeper.logging import bind_request, clear_request, configure_logging, get_logger
from paykeeper.providers.fake import FakeProvider

log = get_logger("paykeeper.api")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    engine = create_engine(settings.database_url)
    sm = create_sessionmaker(engine)
    app.state.engine = engine
    app.state.sessionmaker = sm
    app.state.primary_provider = FakeProvider(
        secret=settings.webhook_secret.get_secret_value(),
        instance="primary",
    )
    log.info("startup", environment=settings.environment)
    try:
        yield
    finally:
        await engine.dispose()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="paykeeper",
        version="0.1.0",
        description="Idempotent charge/refund API with PSP failover and reconciliation.",
        lifespan=_lifespan,
    )
    app.state.settings = settings

    install_error_handlers(app)

    @app.middleware("http")
    async def _request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        bind_request(request_id)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_exception", path=request.url.path)
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "internal error"}},
            )
        finally:
            clear_request()
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(charges.router)
    app.include_router(refunds.router)
    return app
