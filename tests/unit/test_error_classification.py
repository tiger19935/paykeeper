from __future__ import annotations

from paykeeper.providers.base import (
    AmbiguousOutcomeError,
    CardDeclinedError,
    InvalidRequestError,
    NetworkError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def test_retryable_classification() -> None:
    assert NetworkError.retryable is True
    assert ProviderUnavailableError.retryable is True

    assert ProviderTimeoutError.retryable is False
    assert AmbiguousOutcomeError.retryable is False
    assert CardDeclinedError.retryable is False
    assert InvalidRequestError.retryable is False


def test_hierarchy() -> None:
    for e in (
        NetworkError,
        ProviderTimeoutError,
        ProviderUnavailableError,
        AmbiguousOutcomeError,
        CardDeclinedError,
        InvalidRequestError,
    ):
        assert issubclass(e, ProviderError)
