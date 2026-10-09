from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictInt


class ChargeRequest(BaseModel):
    amount: StrictInt = Field(..., gt=0, description="Amount in the currency's minor unit")
    currency: str = Field(..., min_length=3, max_length=3)
    customer_id: str = Field(..., min_length=1, max_length=128)
    payment_method_token: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


class RefundRequest(BaseModel):
    charge_id: str = Field(..., min_length=1)
    amount: StrictInt = Field(..., gt=0)
    reason: str | None = Field(default=None, max_length=255)


class ChargeResponse(BaseModel):
    id: str
    customer_id: str
    amount: int
    currency: str
    status: Literal["pending", "succeeded", "failed"]
    provider: str
    provider_ref: str | None = None
    description: str | None = None
    created_at: datetime


class RefundResponse(BaseModel):
    id: str
    charge_id: str
    amount: int
    currency: str
    status: Literal["pending", "succeeded", "failed"]
    provider: str
    provider_ref: str | None = None
    reason: str | None = None
    created_at: datetime


IdempotencyKeyHeader = Annotated[
    str,
    Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_\-:.]+$"),
]
