from __future__ import annotations

import pytest

from paykeeper.idempotency.fingerprint import fingerprint


def test_key_order_does_not_matter() -> None:
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert fingerprint(a) == fingerprint(b)


def test_different_values_different_digest() -> None:
    assert fingerprint({"amount": 100}) != fingerprint({"amount": 101})


def test_nested_structures() -> None:
    a = {"customer": {"id": "c1"}, "items": [1, 2, 3]}
    b = {"items": [1, 2, 3], "customer": {"id": "c1"}}
    assert fingerprint(a) == fingerprint(b)


def test_float_rejected() -> None:
    with pytest.raises(TypeError):
        fingerprint({"amount": 1.5})


def test_float_rejected_in_nested_list() -> None:
    with pytest.raises(TypeError):
        fingerprint({"items": [{"price": 1.0}]})


def test_bool_not_treated_as_float() -> None:
    fingerprint({"flag": True})


def test_unicode_preserved() -> None:
    assert fingerprint({"name": "€100"}) == fingerprint({"name": "€100"})


def test_stable_across_calls() -> None:
    body = {"customer_id": "c_1", "amount": 100, "currency": "USD"}
    assert fingerprint(body) == fingerprint(body)
    assert len(fingerprint(body)) == 64
