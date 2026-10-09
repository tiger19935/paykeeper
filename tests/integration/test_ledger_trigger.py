from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import Charge, LedgerEntry

pytestmark = pytest.mark.integration


async def _insert_minimal_ledger(session: AsyncSession) -> LedgerEntry:
    charge = Charge(
        id=uuid.uuid4(),
        customer_id="c",
        amount=100,
        currency="USD",
        payment_method_token="pm_card",
        state="succeeded",
        provider="fake",
        provider_ref="ch_test",
        operation_id="op-ledger-test",
    )
    session.add(charge)
    await session.flush()
    entry = LedgerEntry(
        id=uuid.uuid4(),
        entry_type="charge.succeeded",
        amount=100,
        currency="USD",
        charge_id=charge.id,
        provider="fake",
        provider_ref="ch_test",
    )
    session.add(entry)
    await session.flush()
    await session.commit()
    return entry


async def test_update_on_ledger_entries_is_rejected(db_session: AsyncSession) -> None:
    entry = await _insert_minimal_ledger(db_session)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entry.id).values(amount=1)
        )
        await db_session.commit()


async def test_delete_on_ledger_entries_is_rejected(db_session: AsyncSession) -> None:
    entry = await _insert_minimal_ledger(db_session)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry.id))
        await db_session.commit()
