"""Add water asset state history.

Revision ID: f8a6b1c3d9e2
Revises: c5d8e7f2a901
Create Date: 2026-06-05 12:00:00.000000
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "f8a6b1c3d9e2"
down_revision = "c5d8e7f2a901"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "water_asset_state_history",
        sa.Column("water_asset_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("change_type", sa.String(length=20), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("previous_active", sa.Boolean(), nullable=True),
        sa.Column("previous_status", sa.String(length=40), nullable=True),
        sa.Column("previous_water_level", sa.String(length=30), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column("water_level", sa.String(length=30), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('baseline', 'created', 'updated')",
            name="ck_water_asset_state_history_change_type",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["water_asset_id"], ["water_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_water_asset_state_history_asset_changed_at",
        "water_asset_state_history",
        ["water_asset_id", "changed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_water_asset_state_history_changed_at"),
        "water_asset_state_history",
        ["changed_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_water_asset_state_history_change_type"),
        "water_asset_state_history",
        ["change_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_water_asset_state_history_farm_id"),
        "water_asset_state_history",
        ["farm_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_water_asset_state_history_water_asset_id"),
        "water_asset_state_history",
        ["water_asset_id"],
        unique=False,
    )

    history_table = sa.table(
        "water_asset_state_history",
        sa.column("water_asset_id", sa.String(length=36)),
        sa.column("farm_id", sa.String(length=36)),
        sa.column("change_type", sa.String(length=20)),
        sa.column("previous_active", sa.Boolean()),
        sa.column("previous_status", sa.String(length=40)),
        sa.column("previous_water_level", sa.String(length=30)),
        sa.column("active", sa.Boolean()),
        sa.column("status", sa.String(length=40)),
        sa.column("water_level", sa.String(length=30)),
        sa.column("id", sa.String(length=36)),
    )
    existing_assets = op.get_bind().execute(
        sa.text("SELECT id, farm_id, active, status, water_level FROM water_assets")
    )
    baseline_rows = [
        {
            "water_asset_id": row.id,
            "farm_id": row.farm_id,
            "change_type": "baseline",
            "previous_active": None,
            "previous_status": None,
            "previous_water_level": None,
            "active": bool(row.active),
            "status": row.status,
            "water_level": row.water_level,
            "id": str(uuid.uuid4()),
        }
        for row in existing_assets
    ]
    if baseline_rows:
        op.bulk_insert(history_table, baseline_rows)


def downgrade():
    op.drop_index(op.f("ix_water_asset_state_history_water_asset_id"), table_name="water_asset_state_history")
    op.drop_index(op.f("ix_water_asset_state_history_farm_id"), table_name="water_asset_state_history")
    op.drop_index(op.f("ix_water_asset_state_history_change_type"), table_name="water_asset_state_history")
    op.drop_index(op.f("ix_water_asset_state_history_changed_at"), table_name="water_asset_state_history")
    op.drop_index("ix_water_asset_state_history_asset_changed_at", table_name="water_asset_state_history")
    op.drop_table("water_asset_state_history")
