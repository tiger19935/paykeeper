from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from paykeeper.api.deps import SessionDep, SettingsDep
from paykeeper.api.errors import RefundNotFoundError
from paykeeper.api.schemas import RefundRequest
from paykeeper.domain.service import get_refund, process_refund

router = APIRouter(prefix="/v1", tags=["refunds"])


@router.post("/refunds")
async def create_refund(
    body: RefundRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> JSONResponse:
    router_ = request.app.state.provider_router
    status, response = await process_refund(
        session,
        router_,
        idempotency_key=idempotency_key,
        body=body.model_dump(),
        stale_after=timedelta(seconds=settings.idempotency_lock_stale_seconds),
        ttl=timedelta(seconds=settings.idempotency_ttl_seconds),
    )
    return JSONResponse(status_code=status, content=response)


@router.get("/refunds/{refund_id}")
async def read_refund(refund_id: str, session: SessionDep) -> JSONResponse:
    refund = await get_refund(session, refund_id)
    if refund is None:
        raise RefundNotFoundError(f"refund {refund_id} not found")
    return JSONResponse(status_code=200, content=refund)
