from __future__ import annotations

import hashlib
import hmac

import pytest

from paykeeper.webhooks.verify import WebhookVerifyError, verify

_SECRET = "s3cr3t"


def _sign(body: bytes, ts: int, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def test_valid_signature_and_timestamp_accepted() -> None:
    body = b'{"id":"evt_1","type":"charge.succeeded"}'
    ts = 1_234_567_890
    sig = _sign(body, ts)

    result = verify(
        secret=_SECRET,
        body=body,
        signature=sig,
        timestamp=str(ts),
        tolerance_seconds=300,
        now=ts,
    )
    assert result.timestamp == ts
    assert result.body == body


def test_tampered_body_rejected() -> None:
    ts = 1_234_567_890
    sig = _sign(b"original", ts)
    with pytest.raises(WebhookVerifyError, match="bad signature"):
        verify(
            secret=_SECRET,
            body=b"tampered",
            signature=sig,
            timestamp=str(ts),
            tolerance_seconds=300,
            now=ts,
        )


def test_wrong_secret_rejected() -> None:
    body = b"{}"
    ts = 100
    sig = _sign(body, ts, secret="other")
    with pytest.raises(WebhookVerifyError, match="bad signature"):
        verify(
            secret=_SECRET,
            body=body,
            signature=sig,
            timestamp=str(ts),
            tolerance_seconds=300,
            now=ts,
        )


def test_replayed_timestamp_rejected() -> None:
    body = b"{}"
    ts = 100
    sig = _sign(body, ts)
    with pytest.raises(WebhookVerifyError, match="outside tolerance"):
        verify(
            secret=_SECRET,
            body=body,
            signature=sig,
            timestamp=str(ts),
            tolerance_seconds=60,
            now=ts + 3600,
        )


def test_missing_signature_rejected() -> None:
    with pytest.raises(WebhookVerifyError, match="missing signature"):
        verify(
            secret=_SECRET,
            body=b"{}",
            signature="",
            timestamp="100",
            tolerance_seconds=300,
            now=100,
        )


def test_missing_timestamp_rejected() -> None:
    with pytest.raises(WebhookVerifyError, match="missing timestamp"):
        verify(
            secret=_SECRET,
            body=b"{}",
            signature="deadbeef",
            timestamp=None,
            tolerance_seconds=300,
            now=100,
        )


def test_non_integer_timestamp_rejected() -> None:
    with pytest.raises(WebhookVerifyError, match="not an integer"):
        verify(
            secret=_SECRET,
            body=b"{}",
            signature="deadbeef",
            timestamp="abc",
            tolerance_seconds=300,
            now=100,
        )
