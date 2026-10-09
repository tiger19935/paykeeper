"""Provider protocol and error taxonomy.

The taxonomy drives the retry policy. Only `NetworkError` and
`ProviderUnavailableError` are retryable. `ProviderTimeoutError` is NEVER
retried without a provider state lookup — the request may have succeeded
and we would otherwise double-charge. See ADR 0002.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    retryable: bool = False


class NetworkError(ProviderError):
    retryable = True


class ProviderTimeoutError(ProviderError):
    """The request timed out without a response.

    Outcome is ambiguous: it may have succeeded. The caller must consult
    `Provider.get_by_operation_id` before deciding what to do, so this
    error is NOT marked retryable.
    """

    retryable = False


class ProviderUnavailableError(ProviderError):
    """Service responded with a 5xx or 429."""

    retryable = True


class AmbiguousOutcomeError(ProviderError):
    """Signalled by recovery when the provider lookup is itself inconclusive."""

    retryable = False


class CardDeclinedError(ProviderError):
    retryable = False


class InvalidRequestError(ProviderError):
    retryable = False


@dataclass(frozen=True, slots=True)
class ChargeResult:
    provider_ref: str
    status: str
    amount: int
    currency: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class RefundResult:
    provider_ref: str
    status: str
    amount: int
    currency: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class WebhookInfo:
    provider_event_id: str
    event_type: str
    payload: dict[str, object]


@runtime_checkable
class Provider(Protocol):
    name: str

    async def charge(
        self,
        *,
        amount: int,
        currency: str,
        payment_method_token: str,
        customer_id: str,
        operation_id: str,
    ) -> ChargeResult: ...

    async def refund(
        self,
        *,
        charge_provider_ref: str,
        amount: int,
        currency: str,
        operation_id: str,
    ) -> RefundResult: ...

    async def get_by_operation_id(
        self, *, operation_id: str
    ) -> ChargeResult | RefundResult | None: ...

    def verify_webhook(
        self, *, body: bytes, signature: str, timestamp: str | None
    ) -> WebhookInfo: ...
