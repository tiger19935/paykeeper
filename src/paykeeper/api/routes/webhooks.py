from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from paykeeper.api.deps import SessionDep, SettingsDep
from paykeeper.api.errors import InvalidWebhookError
from paykeeper.webhooks.handlers import apply_webhook
from paykeeper.webhooks.verify import WebhookVerifyError, verify

router = APIRouter(prefix="/v1", tags=["webhooks"])


@router.post("/webhooks/{provider}")
async def receive_webhook(
    provider: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    signature: str = Header("", alias="X-Signature"),
    timestamp: str = Header("", alias="X-Signature-Timestamp"),
) -> JSONResponse:
    body = await request.body()
    try:
        verify(
            secret=settings.webhook_secret.get_secret_value(),
            body=body,
            signature=signature,
            timestamp=timestamp,
            tolerance_seconds=settings.webhook_tolerance_seconds,
        )
    except WebhookVerifyError as exc:
        raise InvalidWebhookError(str(exc)) from exc

    try:
        status, applied = await apply_webhook(session, provider=provider, body=body)
    except ValueError as exc:
        raise InvalidWebhookError(str(exc)) from exc

    return JSONResponse(
        status_code=200,
        content={"status": status, "applied": applied},
    )
