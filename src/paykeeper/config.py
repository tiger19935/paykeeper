"""Runtime configuration loaded from the environment.

The service refuses to start if `PAYKEEPER_WEBHOOK_SECRET` is unset — webhook
signature verification would otherwise silently degrade to no-op.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]
ProviderName = Literal["fake", "stripe"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAYKEEPER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "dev"
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    database_url: str = Field(
        default="postgresql+asyncpg://paykeeper:paykeeper@localhost:5432/paykeeper",
        description="Async SQLAlchemy DSN (postgresql+asyncpg://…).",
    )

    webhook_secret: SecretStr = Field(
        ...,
        description="Shared secret for HMAC webhook verification.",
    )
    webhook_tolerance_seconds: int = 300

    primary_provider: ProviderName = "fake"
    secondary_provider: ProviderName | None = "fake"

    stripe_api_key: SecretStr | None = None
    stripe_base_url: str = "https://api.stripe.com"
    stripe_timeout_seconds: float = 10.0

    idempotency_lock_stale_seconds: int = 30
    idempotency_ttl_seconds: int = 24 * 60 * 60

    retry_max_attempts: int = 4
    retry_base_delay_ms: int = 50
    retry_max_delay_ms: int = 2_000

    breaker_failure_threshold: int = 5
    breaker_window_seconds: int = 60
    breaker_open_seconds: int = 30

    http_port: int = 8000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # mypy sees required fields with no defaults and flags call-arg;
    # pydantic-settings loads them from the environment at __init__ time.
    return Settings()  # type: ignore[call-arg]
