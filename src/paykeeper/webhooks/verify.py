"""Webhook signature verification.

Signed payload format (Stripe-flavoured):
    hex_sig = HMAC_SHA256(secret, f"{timestamp}.{body}")

Verification rejects:
- missing or malformed headers
- bad signature (constant-time compare)
- a timestamp more than `tolerance_seconds` away from the server's clock
  (replay defence against captured-then-replayed requests)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass


class WebhookVerifyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WebhookVerifyResult:
    timestamp: int
    body: bytes


def verify(
    *,
    secret: str,
    body: bytes,
    signature: str,
    timestamp: str | None,
    tolerance_seconds: int,
    now: int | None = None,
) -> WebhookVerifyResult:
    if not signature:
        raise WebhookVerifyError("missing signature")
    if not timestamp:
        raise WebhookVerifyError("missing timestamp")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise WebhookVerifyError("timestamp not an integer") from exc

    _now = int(time.time()) if now is None else now
    if abs(_now - ts) > tolerance_seconds:
        raise WebhookVerifyError(f"timestamp outside tolerance ({_now - ts}s off)")

    signed = f"{ts}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerifyError("bad signature")

    return WebhookVerifyResult(timestamp=ts, body=body)
