from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from paykeeper.domain.models import Charge, Refund
from paykeeper.webhooks.handlers import apply_webhook

pytestmark = pytest.mark.integration


async def _insert_charge(session: AsyncSession, ref: str, state: str = "pending") -> uuid.UUID:
    cid = uuid.uuid4()
    session.add(
        Charge(
            id=cid,
            customer_id="c",
            amount=100,
            currency="USD",
            payment_method_token="pm_card",
            state=state,
            provider="fake:primary",
            provider_ref=ref,
            operation_id=f"op-{cid}",
        )
    )
    await session.commit()
    return cid


async def test_apply_charge_failed(db_session: AsyncSession) -> None:
    await _insert_charge(db_session, ref="ch_f1")
    body = json.dumps(
        {"id": "evt_f1", "type": "charge.failed", "data": {"provider_ref": "ch_f1"}}
    ).encode()
    status, applied = await apply_webhook(db_session, provider="fake:primary", body=body)
    assert status == "applied"
    assert applied is True


async def test_apply_refund_succeeded(db_session: AsyncSession) -> None:
    cid = await _insert_charge(db_session, ref="ch_r1", state="succeeded")
    rid = uuid.uuid4()
    db_session.add(
        Refund(
            id=rid,
            charge_id=cid,
            amount=10,
            currency="USD",
            state="pending",
            provider="fake:primary",
            provider_ref="re_r1",
            operation_id=f"op-r-{rid}",
        )
    )
    await db_session.commit()

    body = json.dumps(
        {"id": "evt_r1", "type": "refund.succeeded", "data": {"provider_ref": "re_r1"}}
    ).encode()
    status, applied = await apply_webhook(db_session, provider="fake:primary", body=body)
    assert status == "applied"
    assert applied is True


async def test_apply_webhook_payload_without_id_rejected(db_session: AsyncSession) -> None:
    body = b'{"type":"charge.succeeded"}'
    with pytest.raises(ValueError, match="missing id/type"):
        await apply_webhook(db_session, provider="fake:primary", body=body)


async def test_apply_webhook_non_json_body_rejected(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        await apply_webhook(db_session, provider="fake:primary", body=b"not json")


async def test_apply_webhook_charge_not_in_local_state(db_session: AsyncSession) -> None:
    body = json.dumps(
        {
            "id": "evt_noop",
            "type": "charge.succeeded",
            "data": {"provider_ref": "ch_unknown"},
        }
    ).encode()
    status, applied = await apply_webhook(db_session, provider="fake:primary", body=body)
    assert status == "stored"
    assert applied is False


async def test_apply_charge_succeeded_already_in_state_is_noop(
    db_session: AsyncSession,
) -> None:
    await _insert_charge(db_session, ref="ch_already", state="succeeded")
    body = json.dumps(
        {
            "id": "evt_noop2",
            "type": "charge.succeeded",
            "data": {"provider_ref": "ch_already"},
        }
    ).encode()
    status, applied = await apply_webhook(db_session, provider="fake:primary", body=body)
    assert status == "stored"
    assert applied is False


async def test_apply_webhook_missing_provider_ref_in_data(
    db_session: AsyncSession,
) -> None:
    body = json.dumps({"id": "evt_x", "type": "charge.succeeded", "data": {}}).encode()
    status, applied = await apply_webhook(db_session, provider="fake:primary", body=body)
    assert status == "stored"
    assert applied is False
