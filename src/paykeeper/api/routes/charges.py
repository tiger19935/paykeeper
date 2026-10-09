from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from paykeeper.api.deps import SessionDep, SettingsDep
from paykeeper.api.errors import ChargeNotFoundError
from paykeeper.api.schemas import ChargeRequest
from paykeeper.domain.service import get_charge, process_charge

router = APIRouter(prefix="/v1", tags=["charges"])


@router.post("/charges")
async def create_charge(
    body: ChargeRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
) -> JSONResponse:
    router_ = request.app.state.provider_router
    status, response = await process_charge(
        session,
        router_,
        idempotency_key=idempotency_key,
        body=body.model_dump(),
        stale_after=timedelta(seconds=settings.idempotency_lock_stale_seconds),
        ttl=timedelta(seconds=settings.idempotency_ttl_seconds),
    )
    return JSONResponse(status_code=status, content=response)


@router.get("/charges/{charge_id}")
async def read_charge(charge_id: str, session: SessionDep) -> JSONResponse:
    charge = await get_charge(session, charge_id)
    if charge is None:
        raise ChargeNotFoundError(f"charge {charge_id} not found")
    return JSONResponse(status_code=200, content=charge)
