from __future__ import annotations

from enum import StrEnum


class ChargeState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RefundState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LedgerEntryType(StrEnum):
    CHARGE_SUCCEEDED = "charge.succeeded"
    REFUND_SUCCEEDED = "refund.succeeded"


class IdempotencyState(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ProviderName(StrEnum):
    FAKE = "fake"
    STRIPE = "stripe"


class OutboxEventType(StrEnum):
    CHARGE_CREATED = "charge.created"
    CHARGE_SUCCEEDED = "charge.succeeded"
    CHARGE_FAILED = "charge.failed"
    REFUND_CREATED = "refund.created"
    REFUND_SUCCEEDED = "refund.succeeded"
    REFUND_FAILED = "refund.failed"


class IdempotencyScope(StrEnum):
    CHARGE = "charge"
    REFUND = "refund"
