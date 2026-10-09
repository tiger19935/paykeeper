from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from paykeeper.providers import (
    CardDeclinedError,
    FakeProvider,
    FakeProviderBehavior,
    InvalidRequestError,
    NetworkError,
    Provider,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakeProvider(), Provider)


async def test_charge_success() -> None:
    p = FakeProvider()
    assert p.name == "fake:primary"
    result = await p.charge(
        amount=100,
        currency="USD",
        payment_method_token="pm_card",
        customer_id="c1",
        operation_id="op-1",
    )
    assert result.status == "succeeded"
    assert result.amount == 100
    assert result.provider_ref.startswith("ch_primary_")


async def test_charge_is_idempotent_by_operation_id() -> None:
    p = FakeProvider()
    a = await p.charge(
        amount=100,
        currency="USD",
        payment_method_token="pm_card",
        customer_id="c1",
        operation_id="op-1",
    )
    b = await p.charge(
        amount=100,
        currency="USD",
        payment_method_token="pm_card",
        customer_id="c1",
        operation_id="op-1",
    )
    assert a is b
    assert len(p.all_charges()) == 1


async def test_card_declined() -> None:
    p = FakeProvider()
    with pytest.raises(CardDeclinedError):
        await p.charge(
            amount=100,
            currency="USD",
            payment_method_token="pm_decline",
            customer_id="c1",
            operation_id="op-x",
        )


async def test_timeout_is_ambiguous_outcome_but_charge_stored() -> None:
    p = FakeProvider()
    with pytest.raises(ProviderTimeoutError):
        await p.charge(
            amount=100,
            currency="USD",
            payment_method_token="pm_timeout",
            customer_id="c1",
            operation_id="op-amb",
        )
    stored = await p.get_by_operation_id(operation_id="op-amb")
    assert stored is not None
    assert getattr(stored, "amount", None) == 100


async def test_network_fail_then_success() -> None:
    beh = FakeProviderBehavior(network_fail_until_attempt={"op-net": 2})
    p = FakeProvider(behavior=beh)

    with pytest.raises(NetworkError):
        await p.charge(
            amount=100,
            currency="USD",
            payment_method_token="pm_card",
            customer_id="c",
            operation_id="op-net",
        )
    with pytest.raises(NetworkError):
        await p.charge(
            amount=100,
            currency="USD",
            payment_method_token="pm_card",
            customer_id="c",
            operation_id="op-net",
        )

    result = await p.charge(
        amount=100,
        currency="USD",
        payment_method_token="pm_card",
        customer_id="c",
        operation_id="op-net",
    )
    assert result.status == "succeeded"


async def test_unavailable() -> None:
    p = FakeProvider(behavior=FakeProviderBehavior(always_unavailable=True))
    with pytest.raises(ProviderUnavailableError):
        await p.charge(
            amount=100,
            currency="USD",
            payment_method_token="pm_card",
            customer_id="c",
            operation_id="op-u",
        )


async def test_refund_ok() -> None:
    p = FakeProvider()
    charge = await p.charge(
        amount=200,
        currency="USD",
        payment_method_token="pm_card",
        customer_id="c",
        operation_id="op-ch",
    )
    refund = await p.refund(
        charge_provider_ref=charge.provider_ref,
        amount=50,
        currency="USD",
        operation_id="op-re",
    )
    assert refund.status == "succeeded"
    assert refund.amount == 50


async def test_refund_unknown_charge_rejected() -> None:
    p = FakeProvider()
    with pytest.raises(InvalidRequestError):
        await p.refund(
            charge_provider_ref="ch_unknown",
            amount=1,
            currency="USD",
            operation_id="op-re",
        )


def test_webhook_verify_valid_and_invalid() -> None:
    p = FakeProvider(secret="s3cret")
    payload = {"id": "evt_1", "type": "charge.succeeded", "data": {"amount": 100}}
    body = json.dumps(payload).encode()
    ts = "1234567890"
    sig = hmac.new(b"s3cret", ts.encode() + b"." + body, hashlib.sha256).hexdigest()

    info = p.verify_webhook(body=body, signature=sig, timestamp=ts)
    assert info.provider_event_id == "evt_1"
    assert info.event_type == "charge.succeeded"

    with pytest.raises(InvalidRequestError):
        p.verify_webhook(body=body, signature="deadbeef", timestamp=ts)
