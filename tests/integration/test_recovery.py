from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from paykeeper.api.errors import ProviderUnavailableError
from paykeeper.domain.models import Charge, IdempotencyKey, LedgerEntry
from paykeeper.domain.service import process_charge
from paykeeper.providers.fake import FakeProvider

pytestmark = pytest.mark.integration


_BODY = {
    "amount": 500,
    "currency": "USD",
    "customer_id": "cust_r",
    "payment_method_token": "pm_timeout",
    "description": "ambiguous",
}


async def test_ambiguous_outcome_recovers_to_single_charge(
    sessionmaker_: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,  # truncate tables
) -> None:
    """Provider stores the charge then raises Timeout: client retries,
    sees STALE, recovery discovers the stored charge and backfills state.
    No double charge."""

    provider = FakeProvider(instance="primary")
    key = "k-amb-1"

    # First try: provider raises ProviderTimeoutError but has stored the charge.
    async with sessionmaker_() as session:
        with pytest.raises(ProviderUnavailableError):
            await process_charge(
                session,
                provider,
                idempotency_key=key,
                body=_BODY,
                stale_after=timedelta(seconds=30),
                ttl=timedelta(hours=24),
            )

    # Age the lock so the next call sees STALE.
    async with sessionmaker_() as session:
        row = (
            await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
        ).scalar_one()
        row.locked_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    # Second try: STALE → recovery path → finds stored provider charge → 201.
    async with sessionmaker_() as session:
        status, body = await process_charge(
            session,
            provider,
            idempotency_key=key,
            body=_BODY,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 201
    assert body["status"] == "succeeded"
    assert body["amount"] == 500

    async with sessionmaker_() as session:
        charges = (await session.execute(select(func.count()).select_from(Charge))).scalar_one()
        entries = (
            await session.execute(select(func.count()).select_from(LedgerEntry))
        ).scalar_one()
    assert charges == 1
    assert entries == 1


async def test_stale_lock_with_no_provider_record_falls_through_to_fresh_charge(
    sessionmaker_: async_sessionmaker[AsyncSession],
    db_session: AsyncSession,
) -> None:
    """If the worker died BEFORE the provider accepted the charge, there
    is no provider record. Recovery deletes the stale key and runs a
    fresh charge."""

    provider = FakeProvider(instance="primary")
    key = "k-amb-fresh"

    # Insert a stale in_progress key by hand (no corresponding provider state).
    from paykeeper.idempotency.fingerprint import fingerprint

    good_body = {**_BODY, "payment_method_token": "pm_card_ok"}

    async with sessionmaker_() as session:
        session.add(
            IdempotencyKey(
                key=key,
                scope=f"charge:{good_body['customer_id']}",
                request_fingerprint=fingerprint(good_body),
                state="in_progress",
                locked_at=datetime.now(UTC) - timedelta(seconds=600),
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await session.commit()

    async with sessionmaker_() as session:
        status, body = await process_charge(
            session,
            provider,
            idempotency_key=key,
            body=good_body,
            stale_after=timedelta(seconds=30),
            ttl=timedelta(hours=24),
        )
    assert status == 201
    assert body["status"] == "succeeded"

    async with sessionmaker_() as session:
        charges = (await session.execute(select(func.count()).select_from(Charge))).scalar_one()
    assert charges == 1
