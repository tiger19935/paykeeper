"""Structured logging via structlog.

Every log line carries `request_id` and (when relevant) `idempotency_key`,
bound through context variables so handlers inherit them without threading
them through every call. Secrets are never bound — `SecretStr` values would
render as `**********` by pydantic and we don't log them anyway.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import cast

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from paykeeper.config import LogFormat

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def configure_logging(level: str = "INFO", fmt: LogFormat = "json") -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_request(request_id: str, **extra: str) -> None:
    _request_id.set(request_id)
    bind_contextvars(request_id=request_id, **extra)


def bind_idempotency_key(key: str) -> None:
    bind_contextvars(idempotency_key=key)


def clear_request() -> None:
    _request_id.set(None)
    clear_contextvars()


def current_request_id() -> str | None:
    return _request_id.get()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
