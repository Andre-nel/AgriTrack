"""Add cash transaction tables.

Revision ID: 4d8f2a1b6c7e
Revises: 3a5c8d1e7f0b
Create Date: 2026-03-22 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4d8f2a1b6c7e"
down_revision = "3a5c8d1e7f0b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cash_transactions",
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("counterparty", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cash_transactions_farm_id"), "cash_transactions", ["farm_id"], unique=False)
    op.create_index(
        op.f("ix_cash_transactions_transaction_date"),
        "cash_transactions",
        ["transaction_date"],
        unique=False,
    )

    op.create_table(
        "cash_transaction_lines",
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("category_code", sa.String(length=60), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("species_scope", sa.String(length=20), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_cash_transaction_lines_amount_positive"),
        sa.CheckConstraint(
            "direction IN ('inflow', 'outflow')",
            name="ck_cash_transaction_lines_direction_allowed",
        ),
        sa.CheckConstraint(
            "species_scope IS NULL OR species_scope IN ('Sheep', 'Cattle', 'Goat')",
            name="ck_cash_transaction_lines_species_scope_allowed",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["cash_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cash_transaction_lines_category_code"),
        "cash_transaction_lines",
        ["category_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cash_transaction_lines_species_scope"),
        "cash_transaction_lines",
        ["species_scope"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cash_transaction_lines_transaction_id"),
        "cash_transaction_lines",
        ["transaction_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_cash_transaction_lines_transaction_id"), table_name="cash_transaction_lines")
    op.drop_index(op.f("ix_cash_transaction_lines_species_scope"), table_name="cash_transaction_lines")
    op.drop_index(op.f("ix_cash_transaction_lines_category_code"), table_name="cash_transaction_lines")
    op.drop_table("cash_transaction_lines")
    op.drop_index(op.f("ix_cash_transactions_transaction_date"), table_name="cash_transactions")
    op.drop_index(op.f("ix_cash_transactions_farm_id"), table_name="cash_transactions")
    op.drop_table("cash_transactions")
