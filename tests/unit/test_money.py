from __future__ import annotations

import pytest

from paykeeper.domain.money import CurrencyMismatchError, Money


def test_add_same_currency() -> None:
    assert Money(100, "USD") + Money(50, "USD") == Money(150, "USD")


def test_sub_same_currency() -> None:
    assert Money(100, "USD") - Money(30, "USD") == Money(70, "USD")


def test_mul_by_int() -> None:
    assert Money(100, "USD") * 3 == Money(300, "USD")


def test_mul_by_float_rejected() -> None:
    with pytest.raises(TypeError):
        Money(100, "USD") * 1.5  # type: ignore[operator]


def test_add_different_currency_rejected() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(100, "USD") + Money(100, "EUR")


def test_currency_normalised_to_upper() -> None:
    m = Money(1, "usd")
    assert m.currency == "USD"


def test_currency_must_be_three_letters() -> None:
    with pytest.raises(ValueError):
        Money(1, "US")
    with pytest.raises(ValueError):
        Money(1, "US1")


def test_amount_must_be_int_not_bool() -> None:
    with pytest.raises(TypeError):
        Money(True, "USD")  # type: ignore[arg-type]


def test_comparisons() -> None:
    assert Money(10, "USD") < Money(20, "USD")
    assert Money(20, "USD") > Money(10, "USD")
    assert Money(10, "USD") <= Money(10, "USD")


def test_is_positive_and_zero() -> None:
    assert Money(5, "USD").is_positive()
    assert not Money(0, "USD").is_positive()
    assert Money(0, "USD").is_zero()


def test_frozen() -> None:
    m = Money(1, "USD")
    with pytest.raises(AttributeError):
        m.amount = 2  # type: ignore[misc]
