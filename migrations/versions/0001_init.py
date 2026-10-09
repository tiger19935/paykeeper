"""initial schema with append-only ledger trigger

Revision ID: 0001
Revises:
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

LEDGER_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION paykeeper_ledger_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only (op=%)', TG_OP
        USING ERRCODE = 'raise_exception';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

LEDGER_TRIGGER = """
CREATE TRIGGER ledger_entries_append_only
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION paykeeper_ledger_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "charges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_method_token", sa.String(255), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_ref", sa.String(255)),
        sa.Column("operation_id", sa.String(128), nullable=False, unique=True),
        sa.Column("failure_reason", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount > 0", name="ck_charges_amount_positive"),
        sa.CheckConstraint("length(currency) = 3", name="ck_charges_currency_len"),
    )
    op.create_index("ix_charges_customer_id", "charges", ["customer_id"])
    op.create_index("ix_charges_provider_ref", "charges", ["provider_ref"])

    op.create_table(
        "refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "charge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charges.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(255)),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_ref", sa.String(255)),
        sa.Column("operation_id", sa.String(128), nullable=False, unique=True),
        sa.Column("failure_reason", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount > 0", name="ck_refunds_amount_positive"),
        sa.CheckConstraint("length(currency) = 3", name="ck_refunds_currency_len"),
    )
    op.create_index("ix_refunds_charge_id", "refunds", ["charge_id"])
    op.create_index("ix_refunds_provider_ref", "refunds", ["provider_ref"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "charge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("charges.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "refund_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("refunds.id", ondelete="RESTRICT"),
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_ref", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_ledger_entries_charge_id", "ledger_entries", ["charge_id"])
    op.create_index("ix_ledger_entries_refund_id", "ledger_entries", ["refund_id"])
    op.execute(LEDGER_TRIGGER_FN)
    op.execute(LEDGER_TRIGGER)

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("scope", sa.String(255), primary_key=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", postgresql.JSONB()),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_webhook_events_provider_event",
        ),
    )
    op.create_index("ix_webhook_events_provider", "webhook_events", ["provider"])

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_published_at", "outbox_events", ["published_at"])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_append_only ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS paykeeper_ledger_append_only()")
    op.drop_index("ix_outbox_events_published_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_webhook_events_provider", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_ledger_entries_refund_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_charge_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_refunds_provider_ref", table_name="refunds")
    op.drop_index("ix_refunds_charge_id", table_name="refunds")
    op.drop_table("refunds")
    op.drop_index("ix_charges_provider_ref", table_name="charges")
    op.drop_index("ix_charges_customer_id", table_name="charges")
    op.drop_table("charges")
