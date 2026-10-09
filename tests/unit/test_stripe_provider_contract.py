from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from paykeeper.providers.base import (
    CardDeclinedError,
    InvalidRequestError,
    ProviderUnavailableError,
)
from paykeeper.providers.stripe import StripeProvider

_API_KEY = "sk_test_dummy"
_WH_SECRET = "whsec_test"


@pytest.fixture
async def provider() -> StripeProvider:
    client = httpx.AsyncClient(base_url="https://api.stripe.com", auth=(_API_KEY, ""))
    p = StripeProvider(
        api_key=_API_KEY,
        webhook_secret=_WH_SECRET,
        base_url="https://api.stripe.com",
        client=client,
    )
    yield p
    await p.aclose()


@respx.mock
async def test_charge_posts_form_with_idempotency_header(provider: StripeProvider) -> None:
    route = respx.post("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ch_test_1",
                "status": "succeeded",
                "amount": 500,
                "currency": "usd",
            },
        )
    )
    result = await provider.charge(
        amount=500,
        currency="USD",
        payment_method_token="tok_visa",
        customer_id="cus_x",
        operation_id="op-1",
    )
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["Idempotency-Key"] == "op-1"
    assert sent.headers["content-type"].startswith("application/x-www-form-urlencoded")
    body = sent.content.decode()
    assert "amount=500" in body
    assert "currency=usd" in body
    assert "metadata%5Boperation_id%5D=op-1" in body
    assert result.provider_ref == "ch_test_1"
    assert result.amount == 500
    assert result.currency == "USD"


@respx.mock
async def test_card_declined_mapped(provider: StripeProvider) -> None:
    respx.post("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(
            402,
            json={
                "error": {
                    "code": "card_declined",
                    "message": "Your card was declined.",
                }
            },
        )
    )
    with pytest.raises(CardDeclinedError, match="declined"):
        await provider.charge(
            amount=100,
            currency="USD",
            payment_method_token="tok_declined",
            customer_id="cus",
            operation_id="op-2",
        )


@respx.mock
async def test_5xx_mapped_to_provider_unavailable(provider: StripeProvider) -> None:
    respx.post("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    with pytest.raises(ProviderUnavailableError):
        await provider.charge(
            amount=100,
            currency="USD",
            payment_method_token="tok",
            customer_id="cus",
            operation_id="op-3",
        )


@respx.mock
async def test_invalid_request_mapped(provider: StripeProvider) -> None:
    respx.post("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "message": "Missing required param: amount.",
                }
            },
        )
    )
    with pytest.raises(InvalidRequestError, match="Missing required param"):
        await provider.charge(
            amount=100,
            currency="USD",
            payment_method_token="tok",
            customer_id="cus",
            operation_id="op-4",
        )


@respx.mock
async def test_refund_posts_charge_and_amount(provider: StripeProvider) -> None:
    route = respx.post("https://api.stripe.com/v1/refunds").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "re_test_1",
                "status": "succeeded",
                "amount": 250,
                "charge": "ch_test_1",
            },
        )
    )
    result = await provider.refund(
        charge_provider_ref="ch_test_1",
        amount=250,
        currency="USD",
        operation_id="op-refund",
    )
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "charge=ch_test_1" in body
    assert "amount=250" in body
    assert result.provider_ref == "re_test_1"
    assert result.amount == 250


@respx.mock
async def test_get_by_operation_id_search(provider: StripeProvider) -> None:
    respx.get("https://api.stripe.com/v1/charges/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "ch_found",
                        "status": "succeeded",
                        "amount": 777,
                        "currency": "usd",
                    }
                ]
            },
        )
    )
    found = await provider.get_by_operation_id(operation_id="op-lookup")
    assert found is not None
    assert found.provider_ref == "ch_found"


def test_webhook_verify_valid_x_signature_headers(provider: StripeProvider) -> None:
    body = json.dumps({"id": "evt_1", "type": "charge.succeeded"}).encode()
    ts = "1234567890"
    sig = hmac.new(_WH_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    info = provider.verify_webhook(body=body, signature=sig, timestamp=ts)
    assert info.provider_event_id == "evt_1"


def test_webhook_verify_rejects_tampered(provider: StripeProvider) -> None:
    body = b"{\"id\":\"evt_x\",\"type\":\"charge.succeeded\"}"
    with pytest.raises(InvalidRequestError):
        provider.verify_webhook(body=body, signature="deadbeef", timestamp="1")
