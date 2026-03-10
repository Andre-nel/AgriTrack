"""Add grazing allocation LSU history table.

Revision ID: a8f3c6d2e4b1
Revises: f2c7d1a4b9e0
Create Date: 2026-03-09 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a8f3c6d2e4b1"
down_revision = "f2c7d1a4b9e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "grazing_allocation_lsu_history",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("mob_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_id", sa.String(length=36), nullable=False),
        sa.Column("grazing_session_id", sa.String(length=36), nullable=False),
        sa.Column("grazing_allocation_id", sa.String(length=36), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allocation_fraction", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("mob_total_lsu", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("allocated_lsu", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "source IN ('backfill_ledger', 'live')",
            name="ck_grazing_lsu_history_source",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_grazing_lsu_history_effective_range",
        ),
        sa.CheckConstraint(
            "allocation_fraction > 0 and allocation_fraction <= 1",
            name="ck_grazing_lsu_history_fraction",
        ),
        sa.CheckConstraint("mob_total_lsu >= 0", name="ck_grazing_lsu_history_mob_total_non_negative"),
        sa.CheckConstraint("allocated_lsu >= 0", name="ck_grazing_lsu_history_allocated_non_negative"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["mob_id"], ["mobs.id"]),
        sa.ForeignKeyConstraint(["paddock_id"], ["paddocks.id"]),
        sa.ForeignKeyConstraint(["grazing_session_id"], ["grazing_sessions.id"]),
        sa.ForeignKeyConstraint(["grazing_allocation_id"], ["grazing_allocations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grazing_allocation_id",
            "effective_from",
            name="uq_grazing_lsu_history_allocation_effective_from",
        ),
    )
    op.create_index(op.f("ix_grazing_allocation_lsu_history_farm_id"), "grazing_allocation_lsu_history", ["farm_id"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_mob_id"), "grazing_allocation_lsu_history", ["mob_id"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_paddock_id"), "grazing_allocation_lsu_history", ["paddock_id"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_grazing_session_id"), "grazing_allocation_lsu_history", ["grazing_session_id"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_grazing_allocation_id"), "grazing_allocation_lsu_history", ["grazing_allocation_id"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_effective_from"), "grazing_allocation_lsu_history", ["effective_from"], unique=False)
    op.create_index(op.f("ix_grazing_allocation_lsu_history_effective_to"), "grazing_allocation_lsu_history", ["effective_to"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_effective_to"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_effective_from"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_grazing_allocation_id"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_grazing_session_id"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_paddock_id"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_mob_id"), table_name="grazing_allocation_lsu_history")
    op.drop_index(op.f("ix_grazing_allocation_lsu_history_farm_id"), table_name="grazing_allocation_lsu_history")
    op.drop_table("grazing_allocation_lsu_history")
