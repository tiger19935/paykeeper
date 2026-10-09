"""Deterministic in-memory provider.

Supports failure injection by `payment_method_token`:
- "pm_decline"    → CardDeclinedError
- "pm_network"    → NetworkError (first N attempts, then success)
- "pm_timeout"    → ProviderTimeoutError but the charge is nonetheless
                    stored (classic AmbiguousOutcome)
- "pm_unavailable" → ProviderUnavailableError
- any other token → immediate success

Separate per-operation behaviour overrides can be registered through
`FakeProviderBehavior`. Thread-safe for the asyncio loop (dict ops are
atomic in Python; no `await` between read-modify-write).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field

from paykeeper.providers.base import (
    CardDeclinedError,
    ChargeResult,
    InvalidRequestError,
    NetworkError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RefundResult,
    WebhookInfo,
)


@dataclass
class FakeProviderBehavior:
    """Per-operation overrides for the fake provider."""

    network_fail_until_attempt: dict[str, int] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    always_unavailable: bool = False
    added_latency_ms: int = 0


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        secret: str = "fake-webhook-secret",  # noqa: S107
        behavior: FakeProviderBehavior | None = None,
        instance: str = "primary",
    ) -> None:
        self._secret = secret
        self.behavior = behavior or FakeProviderBehavior()
        self._charges: dict[str, ChargeResult] = {}
        self._refunds: dict[str, RefundResult] = {}
        self._charges_by_ref: dict[str, ChargeResult] = {}
        self._instance = instance

    async def charge(
        self,
        *,
        amount: int,
        currency: str,
        payment_method_token: str,
        customer_id: str,
        operation_id: str,
    ) -> ChargeResult:
        if amount <= 0:
            raise InvalidRequestError("amount must be positive")
        if self.behavior.added_latency_ms:
            await asyncio.sleep(self.behavior.added_latency_ms / 1000)
        if self.behavior.always_unavailable:
            raise ProviderUnavailableError("fake set to always unavailable")

        prior = self._charges.get(operation_id)
        if prior is not None:
            return prior

        attempt = self.behavior.attempts.get(operation_id, 0) + 1
        self.behavior.attempts[operation_id] = attempt

        until = self.behavior.network_fail_until_attempt.get(operation_id)
        if until is not None and attempt <= until:
            raise NetworkError(f"fake network failure attempt {attempt}/{until}")

        token = payment_method_token
        if token == "pm_decline":  # noqa: S105
            raise CardDeclinedError("card_declined")
        if token == "pm_unavailable":  # noqa: S105
            raise ProviderUnavailableError("fake unavailable")
        if token == "pm_network":  # noqa: S105
            raise NetworkError("fake network error")
        if token == "pm_invalid":  # noqa: S105
            raise InvalidRequestError("invalid payment method")

        provider_ref = f"ch_{self._instance}_{uuid.uuid4().hex[:12]}"
        result = ChargeResult(
            provider_ref=provider_ref,
            status="succeeded",
            amount=amount,
            currency=currency,
            operation_id=operation_id,
        )
        self._charges[operation_id] = result
        self._charges_by_ref[provider_ref] = result

        if token == "pm_timeout":  # noqa: S105
            raise ProviderTimeoutError("fake timeout (charge nevertheless stored)")

        return result

    async def refund(
        self,
        *,
        charge_provider_ref: str,
        amount: int,
        currency: str,
        operation_id: str,
    ) -> RefundResult:
        charge = self._charges_by_ref.get(charge_provider_ref)
        if charge is None:
            raise InvalidRequestError("unknown charge")
        if amount <= 0 or amount > charge.amount:
            raise InvalidRequestError("refund amount out of range")

        prior = self._refunds.get(operation_id)
        if prior is not None:
            return prior

        provider_ref = f"re_{self._instance}_{uuid.uuid4().hex[:12]}"
        result = RefundResult(
            provider_ref=provider_ref,
            status="succeeded",
            amount=amount,
            currency=currency,
            operation_id=operation_id,
        )
        self._refunds[operation_id] = result
        return result

    async def get_by_operation_id(self, *, operation_id: str) -> ChargeResult | RefundResult | None:
        return self._charges.get(operation_id) or self._refunds.get(operation_id)

    def verify_webhook(self, *, body: bytes, signature: str, timestamp: str | None) -> WebhookInfo:
        signed = (timestamp or "").encode() + b"." + body
        expected = hmac.new(self._secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise InvalidRequestError("bad signature")
        payload = json.loads(body.decode("utf-8"))
        return WebhookInfo(
            provider_event_id=str(payload["id"]),
            event_type=str(payload["type"]),
            payload=payload,
        )

    def inject_charge(self, charge: ChargeResult) -> None:
        self._charges[charge.operation_id] = charge
        self._charges_by_ref[charge.provider_ref] = charge

    def all_charges(self) -> list[ChargeResult]:
        return list(self._charges.values())
