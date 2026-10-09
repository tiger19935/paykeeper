"""Canonical SHA-256 fingerprint of a request body.

Canonicalisation: UTF-8 encoded JSON with sorted keys, no whitespace, no
ensure_ascii coercion. Integers and bools render as themselves; floats are
rejected — they have no place in a money request body.

Two equivalent request bodies must produce the same digest, so key order
and insignificant whitespace must not affect it. A float literal in the
payload raises `TypeError` rather than silently producing a stable but
precision-wrong hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _reject_floats(value: Any) -> Any:
    if isinstance(value, float) and not isinstance(value, bool):
        raise TypeError("float values are not permitted in idempotency payloads")
    if isinstance(value, dict):
        return {k: _reject_floats(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_reject_floats(v) for v in value]
    return value


def fingerprint(body: dict[str, Any]) -> str:
    cleaned = _reject_floats(body)
    canonical = json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
