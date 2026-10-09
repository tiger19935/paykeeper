from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from paykeeper.api.deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: SessionDep) -> JSONResponse:
    try:
        await session.execute(text("select 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": str(exc)},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})
