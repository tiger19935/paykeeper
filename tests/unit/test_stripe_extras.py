from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest
import respx

from paykeeper.providers.base import (
    InvalidRequestError,
    NetworkError,
    ProviderTimeoutError,
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
async def test_charge_timeout_mapped_to_provider_timeout(provider: StripeProvider) -> None:
    respx.post("https://api.stripe.com/v1/charges").mock(
        side_effect=httpx.TimeoutException("read timeout")
    )
    with pytest.raises(ProviderTimeoutError):
        await provider.charge(
            amount=1,
            currency="USD",
            payment_method_token="t",
            customer_id="c",
            operation_id="op",
        )


@respx.mock
async def test_charge_network_error_mapped(provider: StripeProvider) -> None:
    respx.post("https://api.stripe.com/v1/charges").mock(
        side_effect=httpx.ConnectError("conn refused")
    )
    with pytest.raises(NetworkError):
        await provider.charge(
            amount=1,
            currency="USD",
            payment_method_token="t",
            customer_id="c",
            operation_id="op",
        )


@respx.mock
async def test_search_empty_returns_none(provider: StripeProvider) -> None:
    respx.get("https://api.stripe.com/v1/charges/search").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    assert await provider.get_by_operation_id(operation_id="op-x") is None


@respx.mock
async def test_search_5xx_mapped(provider: StripeProvider) -> None:
    respx.get("https://api.stripe.com/v1/charges/search").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailableError):
        await provider.get_by_operation_id(operation_id="op-x")


@respx.mock
async def test_search_timeout_mapped(provider: StripeProvider) -> None:
    respx.get("https://api.stripe.com/v1/charges/search").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    with pytest.raises(ProviderTimeoutError):
        await provider.get_by_operation_id(operation_id="op-x")


@respx.mock
async def test_search_network_mapped(provider: StripeProvider) -> None:
    respx.get("https://api.stripe.com/v1/charges/search").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(NetworkError):
        await provider.get_by_operation_id(operation_id="op-x")


def test_webhook_verify_stripe_formatted_header(provider: StripeProvider) -> None:
    body = b'{"id":"evt_1","type":"charge.succeeded"}'
    ts = "1234567890"
    sig = hmac.new(_WH_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    info = provider.verify_webhook(body=body, signature=header, timestamp=None)
    assert info.provider_event_id == "evt_1"


def test_webhook_verify_missing_both_fails(provider: StripeProvider) -> None:
    with pytest.raises(InvalidRequestError, match="missing"):
        provider.verify_webhook(body=b"{}", signature="", timestamp=None)
