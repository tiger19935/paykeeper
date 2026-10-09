from __future__ import annotations

import pytest
from pydantic import ValidationError

from paykeeper.config import Settings


def test_webhook_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAYKEEPER_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYKEEPER_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("PAYKEEPER_RETRY_MAX_ATTEMPTS", "7")

    s = Settings()  # type: ignore[call-arg]

    assert s.retry_max_attempts == 7
    assert s.webhook_secret.get_secret_value() == "s3cret"


def test_secret_is_not_rendered_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYKEEPER_WEBHOOK_SECRET", "super-secret-value")
    s = Settings()  # type: ignore[call-arg]
    assert "super-secret-value" not in repr(s)
