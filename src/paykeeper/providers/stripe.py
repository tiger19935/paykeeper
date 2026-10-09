"""Stripe provider over the raw REST API.

Deliberately no `stripe` SDK: the HTTP contract is the interesting bit and
reviewers can see each request. The provider forwards our idempotency key
to Stripe via `Idempotency-Key` so Stripe deduplicates on their side too.

Error mapping — status/code → paykeeper exception:
    4xx with code=card_declined         → CardDeclinedError
    400 / 422 (other)                   → InvalidRequestError
    429 / 5xx                           → ProviderUnavailableError  (retryable)
    httpx.TimeoutException              → ProviderTimeoutError      (ambiguous)
    httpx.NetworkError / ConnectError   → NetworkError              (retryable)
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

import httpx

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

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


class StripeProvider:
    """Minimal Stripe HTTP client (Charges API + Refunds API)."""

    def __init__(
        self,
        *,
        api_key: str,
        webhook_secret: str,
        base_url: str = "https://api.stripe.com",
        timeout_seconds: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = "stripe"
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            auth=(api_key, ""),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def charge(
        self,
        *,
        amount: int,
        currency: str,
        payment_method_token: str,
        customer_id: str,
        operation_id: str,
    ) -> ChargeResult:
        form = {
            "amount": str(amount),
            "currency": currency.lower(),
            "source": payment_method_token,
            "customer": customer_id,
            "metadata[operation_id]": operation_id,
        }
        payload = await self._post("/v1/charges", form=form, idem_key=operation_id)
        return ChargeResult(
            provider_ref=str(payload["id"]),
            status=str(payload.get("status", "succeeded")),
            amount=int(payload["amount"]),
            currency=str(payload["currency"]).upper(),
            operation_id=operation_id,
        )

    async def refund(
        self,
        *,
        charge_provider_ref: str,
        amount: int,
        currency: str,
        operation_id: str,
    ) -> RefundResult:
        form = {
            "charge": charge_provider_ref,
            "amount": str(amount),
            "metadata[operation_id]": operation_id,
        }
        payload = await self._post("/v1/refunds", form=form, idem_key=operation_id)
        return RefundResult(
            provider_ref=str(payload["id"]),
            status=str(payload.get("status", "succeeded")),
            amount=int(payload["amount"]),
            currency=currency.upper(),
            operation_id=operation_id,
        )

    async def get_by_operation_id(
        self, *, operation_id: str
    ) -> ChargeResult | RefundResult | None:
        """Search Stripe for a prior charge created with this operation_id."""

        try:
            resp = await self._client.get(
                "/v1/charges/search",
                params={"query": f"metadata['operation_id']:'{operation_id}'"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(str(exc)) from exc

        if resp.status_code >= 500 or resp.status_code == 429:
            raise ProviderUnavailableError(f"status {resp.status_code}")
        if resp.status_code >= 400:
            raise InvalidRequestError(resp.text)

        data = resp.json().get("data") or []
        if not data:
            return None
        payload = data[0]
        return ChargeResult(
            provider_ref=str(payload["id"]),
            status=str(payload.get("status", "succeeded")),
            amount=int(payload["amount"]),
            currency=str(payload["currency"]).upper(),
            operation_id=operation_id,
        )

    def verify_webhook(
        self, *, body: bytes, signature: str, timestamp: str | None
    ) -> WebhookInfo:
        """Verify a Stripe-flavoured signature header.

        Stripe's actual header is `Stripe-Signature: t=<ts>,v1=<sig>,...`;
        we accept both that and the simpler `X-Signature`/`X-Signature-Timestamp`
        pair that paykeeper's inbound webhook route standardises on. The
        FastAPI route always passes the explicit timestamp.
        """

        if timestamp is None:
            # Try to pull t=/v1= from a Stripe-formatted header.
            parts = {k.strip(): v for k, _, v in (p.partition("=") for p in signature.split(","))}
            timestamp = parts.get("t")
            signature = parts.get("v1", "")

        if not timestamp or not signature:
            raise InvalidRequestError("missing timestamp or signature")

        signed = f"{timestamp}.".encode() + body
        expected = hmac.new(
            self._webhook_secret.encode(), signed, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise InvalidRequestError("bad signature")

        import json

        payload = json.loads(body.decode("utf-8"))
        return WebhookInfo(
            provider_event_id=str(payload["id"]),
            event_type=str(payload["type"]),
            payload=payload,
        )

    async def _post(
        self, path: str, *, form: Mapping[str, str], idem_key: str
    ) -> dict[str, Any]:
        try:
            resp = await self._client.post(
                path,
                data=form,
                headers={**_FORM_HEADERS, "Idempotency-Key": idem_key},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(str(exc)) from exc
        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailableError(f"status {resp.status_code}")
        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code") or err.get("type") or ""
            message = err.get("message") or resp.text
            if code == "card_declined":
                raise CardDeclinedError(message)
            raise InvalidRequestError(f"{code}: {message}")
        return resp.json()  # type: ignore[no-any-return]
