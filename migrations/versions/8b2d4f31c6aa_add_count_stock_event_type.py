"""Add count stock event type.

Revision ID: 8b2d4f31c6aa
Revises: 6f3e2c1a9d4b
Create Date: 2026-02-27 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8b2d4f31c6aa"
down_revision = "6f3e2c1a9d4b"
branch_labels = None
depends_on = None


OLD_EVENT_VALUES = (
    "birth",
    "purchase",
    "transfer_in",
    "adjustment_in",
    "death",
    "sale",
    "missing",
    "transfer_out",
    "adjustment_out",
)

NEW_EVENT_VALUES = (
    "birth",
    "purchase",
    "transfer_in",
    "adjustment_in",
    "count",
    "death",
    "sale",
    "missing",
    "transfer_out",
    "adjustment_out",
)


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TYPE stockeventtype ADD VALUE IF NOT EXISTS 'count'")
        return

    if dialect == "sqlite":
        with op.batch_alter_table("stock_ledger_entries", schema=None, recreate="always") as batch_op:
            batch_op.alter_column(
                "event_type",
                existing_type=sa.Enum(*OLD_EVENT_VALUES, name="stockeventtype"),
                type_=sa.Enum(*NEW_EVENT_VALUES, name="stockeventtype"),
                existing_nullable=False,
            )
        return

    with op.batch_alter_table("stock_ledger_entries", schema=None) as batch_op:
        batch_op.alter_column(
            "event_type",
            existing_type=sa.Enum(*OLD_EVENT_VALUES, name="stockeventtype"),
            type_=sa.Enum(*NEW_EVENT_VALUES, name="stockeventtype"),
            existing_nullable=False,
        )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        raise RuntimeError("Downgrade not supported for PostgreSQL enum value removal (stockeventtype.count).")

    count_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM stock_ledger_entries WHERE event_type = 'count'")
    ).scalar()
    if count_rows:
        raise RuntimeError("Cannot downgrade while stock_ledger_entries contains event_type='count' rows.")

    with op.batch_alter_table("stock_ledger_entries", schema=None, recreate="always") as batch_op:
        batch_op.alter_column(
            "event_type",
            existing_type=sa.Enum(*NEW_EVENT_VALUES, name="stockeventtype"),
            type_=sa.Enum(*OLD_EVENT_VALUES, name="stockeventtype"),
            existing_nullable=False,
        )
