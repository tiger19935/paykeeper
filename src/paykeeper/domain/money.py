"""Money value object.

Amounts are stored and manipulated as integers in the currency's minor unit
(cents, pence, kopecks). We never represent money with `float`: binary
floats cannot exactly represent 1/100, so the first FX conversion or sum
would silently accumulate rounding error. A test under tests/unit asserts
that no file under `domain/`, `ledger/`, or `providers/` references the
`float` builtin or a float literal.

Operations are only defined between `Money` values of the same currency;
mixing currencies raises `CurrencyMismatch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_ISO_LEN: Final = 3


class CurrencyMismatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypeError("amount must be int in minor units")
        if len(self.currency) != _ISO_LEN or not self.currency.isalpha():
            raise ValueError(f"currency must be a 3-letter ISO 4217 code, got {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("Money can only be multiplied by int")
        return Money(self.amount * factor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def is_positive(self) -> bool:
        return self.amount > 0

    def is_zero(self) -> bool:
        return self.amount == 0

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
