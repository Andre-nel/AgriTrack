"""Add livestock trade tables.

Revision ID: 3d7e9f1a4c82
Revises: 2c4f6a8b0d1e
Create Date: 2026-07-09 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3d7e9f1a4c82"
down_revision = "2c4f6a8b0d1e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "livestock_trades",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("trade_type", sa.String(length=20), nullable=False),
        sa.Column("counterparty", sa.String(length=120), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("vat_rate", sa.Numeric(precision=7, scale=4), nullable=False),
        sa.Column("animal_group_type_id", sa.String(length=36), nullable=False),
        sa.Column("pricing_model", sa.String(length=20), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("price_per_head", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("goat_count", sa.Integer(), nullable=True),
        sa.Column("hair_lengths", sa.String(length=250), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("cash_transaction_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("goat_count IS NULL OR goat_count > 0", name="ck_livestock_trade_goat_count_positive"),
        sa.CheckConstraint("price_per_head IS NULL OR price_per_head > 0", name="ck_livestock_trade_price_per_head_positive"),
        sa.CheckConstraint("price_per_kg IS NULL OR price_per_kg > 0", name="ck_livestock_trade_price_per_kg_positive"),
        sa.CheckConstraint("pricing_model IN ('weight', 'head')", name="ck_livestock_trade_pricing_model_allowed"),
        sa.CheckConstraint("trade_type IN ('sale', 'purchase')", name="ck_livestock_trade_type_allowed"),
        sa.CheckConstraint("vat_rate >= 0", name="ck_livestock_trade_vat_non_negative"),
        sa.ForeignKeyConstraint(["animal_group_type_id"], ["animal_group_types.id"]),
        sa.ForeignKeyConstraint(["cash_transaction_id"], ["cash_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_livestock_trades_animal_group_type_id"), "livestock_trades", ["animal_group_type_id"], unique=False)
    op.create_index(op.f("ix_livestock_trades_cash_transaction_id"), "livestock_trades", ["cash_transaction_id"], unique=True)
    op.create_index(op.f("ix_livestock_trades_trade_date"), "livestock_trades", ["trade_date"], unique=False)
    op.create_index(op.f("ix_livestock_trades_trade_type"), "livestock_trades", ["trade_type"], unique=False)

    op.create_table(
        "livestock_trade_farms",
        sa.Column("livestock_trade_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_farm_sort_non_negative"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["livestock_trade_id"], ["livestock_trades.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("livestock_trade_id", "farm_id", name="uq_livestock_trade_farm"),
    )
    op.create_index(op.f("ix_livestock_trade_farms_farm_id"), "livestock_trade_farms", ["farm_id"], unique=False)
    op.create_index(op.f("ix_livestock_trade_farms_livestock_trade_id"), "livestock_trade_farms", ["livestock_trade_id"], unique=False)

    op.create_table(
        "livestock_trade_weights",
        sa.Column("livestock_trade_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("weight_kg", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_livestock_trade_weight_sequence_non_negative"),
        sa.CheckConstraint("weight_kg > 0", name="ck_livestock_trade_weight_positive"),
        sa.ForeignKeyConstraint(["livestock_trade_id"], ["livestock_trades.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("livestock_trade_id", "sequence", name="uq_livestock_trade_weight_sequence"),
    )
    op.create_index(op.f("ix_livestock_trade_weights_livestock_trade_id"), "livestock_trade_weights", ["livestock_trade_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_livestock_trade_weights_livestock_trade_id"), table_name="livestock_trade_weights")
    op.drop_table("livestock_trade_weights")
    op.drop_index(op.f("ix_livestock_trade_farms_livestock_trade_id"), table_name="livestock_trade_farms")
    op.drop_index(op.f("ix_livestock_trade_farms_farm_id"), table_name="livestock_trade_farms")
    op.drop_table("livestock_trade_farms")
    op.drop_index(op.f("ix_livestock_trades_trade_type"), table_name="livestock_trades")
    op.drop_index(op.f("ix_livestock_trades_trade_date"), table_name="livestock_trades")
    op.drop_index(op.f("ix_livestock_trades_cash_transaction_id"), table_name="livestock_trades")
    op.drop_index(op.f("ix_livestock_trades_animal_group_type_id"), table_name="livestock_trades")
    op.drop_table("livestock_trades")
