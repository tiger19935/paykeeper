from __future__ import annotations

import io
import json
import logging

import structlog

from paykeeper.logging import bind_request, clear_request, configure_logging, get_logger


def test_structlog_emits_json_with_request_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [handler], raising=False)
    root.setLevel(logging.INFO)

    configure_logging(level="INFO", fmt="json")
    try:
        bind_request("req-123")
        log = get_logger("test")
        log.info("hello", event_detail=42)
    finally:
        clear_request()
        structlog.reset_defaults()

    line = buf.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["request_id"] == "req-123"
    assert record["event"] == "hello"
    assert record["event_detail"] == 42
    assert record["level"] == "info"
