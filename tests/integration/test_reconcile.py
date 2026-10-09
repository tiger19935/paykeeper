from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import Charge
from paykeeper.reconcile.runner import Mismatch, format_report, reconcile_charges

pytestmark = pytest.mark.integration


async def _seed(session: AsyncSession, op: str, amount: int, ref: str) -> Charge:
    c = Charge(
        id=uuid.uuid4(),
        customer_id="c",
        amount=amount,
        currency="USD",
        payment_method_token="pm_card",
        state="succeeded",
        provider="fake:primary",
        provider_ref=ref,
        operation_id=op,
    )
    session.add(c)
    await session.flush()
    return c


async def test_reconcile_no_mismatch(db_session: AsyncSession) -> None:
    await _seed(db_session, op="op-eq", amount=100, ref="ch_eq")
    await db_session.commit()

    async def fetch(_: timedelta) -> list[dict[str, Any]]:
        return [
            {
                "operation_id": "op-eq",
                "provider_ref": "ch_eq",
                "amount": 100,
                "currency": "USD",
                "status": "succeeded",
            }
        ]

    mismatches = await reconcile_charges(db_session, fetch_remote=fetch, since=timedelta(days=1))
    assert mismatches == []
    assert "no mismatches" in format_report(mismatches)


async def test_reconcile_missing_local(db_session: AsyncSession) -> None:
    async def fetch(_: timedelta) -> list[dict[str, Any]]:
        return [
            {
                "operation_id": "op-ghost",
                "provider_ref": "ch_g",
                "amount": 1,
                "currency": "USD",
                "status": "succeeded",
            }
        ]

    mismatches = await reconcile_charges(db_session, fetch_remote=fetch, since=timedelta(days=1))
    kinds = {m.kind for m in mismatches}
    assert kinds == {"missing_local"}


async def test_reconcile_missing_remote(db_session: AsyncSession) -> None:
    await _seed(db_session, op="op-only-local", amount=50, ref="ch_x")
    await db_session.commit()

    async def fetch(_: timedelta) -> list[dict[str, Any]]:
        return []

    mismatches = await reconcile_charges(db_session, fetch_remote=fetch, since=timedelta(days=1))
    kinds = {m.kind for m in mismatches}
    assert kinds == {"missing_remote"}


async def test_reconcile_amount_mismatch(db_session: AsyncSession) -> None:
    await _seed(db_session, op="op-amt", amount=100, ref="ch_a")
    await db_session.commit()

    async def fetch(_: timedelta) -> list[dict[str, Any]]:
        return [
            {
                "operation_id": "op-amt",
                "provider_ref": "ch_a",
                "amount": 101,
                "currency": "USD",
                "status": "succeeded",
            }
        ]

    mismatches = await reconcile_charges(db_session, fetch_remote=fetch, since=timedelta(days=1))
    assert any(m.kind == "amount_mismatch" for m in mismatches)


async def test_reconcile_skips_entries_older_than_window(
    db_session: AsyncSession,
) -> None:
    # Insert and backdate the row by raw SQL since the ORM uses server_default.
    c = await _seed(db_session, op="op-old", amount=10, ref="ch_old")
    from sqlalchemy import update

    await db_session.execute(
        update(Charge)
        .where(Charge.id == c.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=30))
    )
    await db_session.commit()

    async def fetch(_: timedelta) -> list[dict[str, Any]]:
        return []

    mismatches = await reconcile_charges(db_session, fetch_remote=fetch, since=timedelta(days=1))
    assert mismatches == []


def test_format_report_is_table_like() -> None:
    report = format_report(
        [Mismatch(kind="missing_local", operation_id="op-1", local="(none)", remote="100 USD")]
    )
    assert "missing_local" in report
    assert "op-1" in report
    assert "1 mismatch(es)" in report
