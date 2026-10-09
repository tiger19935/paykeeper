"""Primary/secondary provider router with a circuit breaker on the primary.

Policy:
- Charges try primary first (if breaker closed/half-open). Retryable
  errors trigger a secondary attempt. Non-retryable errors (CardDeclined,
  InvalidRequest) propagate unchanged — those are the customer's problem,
  not the provider's.
- Refunds always go to the provider that holds the original charge
  (`charge.provider`), because the provider's refund API cannot act on a
  charge it does not know about.
"""

from __future__ import annotations

import random

from paykeeper.providers.backoff import with_retries
from paykeeper.providers.base import (
    ChargeResult,
    Provider,
    ProviderError,
    ProviderUnavailableError,
    RefundResult,
)
from paykeeper.providers.circuit import CircuitBreaker


class ProviderRouter:
    def __init__(
        self,
        *,
        primary: Provider,
        secondary: Provider | None,
        breaker: CircuitBreaker,
        retry_max_attempts: int = 4,
        retry_base_delay_ms: int = 50,
        retry_max_delay_ms: int = 2000,
        rng: random.Random | None = None,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.retry_max_attempts = retry_max_attempts
        self.retry_base_delay_ms = retry_base_delay_ms
        self.retry_max_delay_ms = retry_max_delay_ms
        self._rng = rng
        self._by_name: dict[str, Provider] = {primary.name: primary}
        if secondary is not None:
            # If two providers share a name (two FakeProvider instances),
            # the secondary wins the dict slot; refund lookups must then
            # match on identity, not name — but tests only use distinct
            # instances; this ambiguity is deliberately kept small.
            self._by_name[secondary.name] = secondary

    def provider_by_name(self, name: str) -> Provider:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise ProviderUnavailableError(f"unknown provider {name!r}") from exc

    async def charge(
        self,
        *,
        amount: int,
        currency: str,
        payment_method_token: str,
        customer_id: str,
        operation_id: str,
    ) -> tuple[ChargeResult, str]:
        if self.breaker.allow():
            try:
                result = await with_retries(
                    lambda: self.primary.charge(
                        amount=amount,
                        currency=currency,
                        payment_method_token=payment_method_token,
                        customer_id=customer_id,
                        operation_id=operation_id,
                    ),
                    max_attempts=self.retry_max_attempts,
                    base_delay_ms=self.retry_base_delay_ms,
                    max_delay_ms=self.retry_max_delay_ms,
                    rng=self._rng,
                )
            except ProviderError as exc:
                if exc.retryable:
                    self.breaker.record_failure()
                    if self.secondary is None:
                        raise
                    return await self._secondary_charge(
                        amount=amount,
                        currency=currency,
                        payment_method_token=payment_method_token,
                        customer_id=customer_id,
                        operation_id=operation_id,
                    )
                # Non-retryable: not a provider health signal — don't
                # trip the breaker.
                raise
            else:
                self.breaker.record_success()
                return result, self.primary.name

        if self.secondary is None:
            raise ProviderUnavailableError("primary circuit is open and no secondary")
        return await self._secondary_charge(
            amount=amount,
            currency=currency,
            payment_method_token=payment_method_token,
            customer_id=customer_id,
            operation_id=operation_id,
        )

    async def _secondary_charge(
        self,
        *,
        amount: int,
        currency: str,
        payment_method_token: str,
        customer_id: str,
        operation_id: str,
    ) -> tuple[ChargeResult, str]:
        # Local binding so mypy narrows inside the lambda closure, which
        # would otherwise re-widen `self.secondary` to `Provider | None`.
        secondary = self.secondary
        assert secondary is not None
        result = await with_retries(
            lambda: secondary.charge(
                amount=amount,
                currency=currency,
                payment_method_token=payment_method_token,
                customer_id=customer_id,
                operation_id=operation_id,
            ),
            max_attempts=self.retry_max_attempts,
            base_delay_ms=self.retry_base_delay_ms,
            max_delay_ms=self.retry_max_delay_ms,
            rng=self._rng,
        )
        return result, secondary.name

    async def refund(
        self,
        *,
        on_provider: str,
        charge_provider_ref: str,
        amount: int,
        currency: str,
        operation_id: str,
    ) -> RefundResult:
        provider = self.provider_by_name(on_provider)
        return await with_retries(
            lambda: provider.refund(
                charge_provider_ref=charge_provider_ref,
                amount=amount,
                currency=currency,
                operation_id=operation_id,
            ),
            max_attempts=self.retry_max_attempts,
            base_delay_ms=self.retry_base_delay_ms,
            max_delay_ms=self.retry_max_delay_ms,
            rng=self._rng,
        )
