"""Exception types + handlers for the HTTP layer.

Domain code raises these; the FastAPI handler translates them into stable
HTTP responses. 409 is used for both fingerprint mismatch and in-flight
duplicates — the `code` field tells them apart for machine consumers.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IdempotencyMismatchError(ApiError):
    status_code = 409
    code = "idempotency_key_reused"


class IdempotencyInFlightError(ApiError):
    status_code = 409
    code = "idempotency_in_flight"


class ChargeNotFoundError(ApiError):
    status_code = 404
    code = "charge_not_found"


class RefundNotFoundError(ApiError):
    status_code = 404
    code = "refund_not_found"


class RefundExceedsBalanceError(ApiError):
    status_code = 422
    code = "refund_exceeds_balance"


class ProviderUnavailableError(ApiError):
    status_code = 502
    code = "provider_unavailable"


class CardDeclinedError(ApiError):
    status_code = 402
    code = "card_declined"


class InvalidWebhookError(ApiError):
    status_code = 400
    code = "invalid_webhook"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
