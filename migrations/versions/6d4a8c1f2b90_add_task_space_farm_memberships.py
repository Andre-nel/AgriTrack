"""Add task space farm memberships.

Revision ID: 6d4a8c1f2b90
Revises: 5a1c9e7d2b44
Create Date: 2026-08-15 12:00:00.000000
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "6d4a8c1f2b90"
down_revision = "5a1c9e7d2b44"
branch_labels = None
depends_on = None


def _backfill_primary_farms() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, farm_id FROM task_spaces WHERE farm_id IS NOT NULL")).fetchall()
    for space_id, farm_id in rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO task_space_farms (
                    space_id, farm_id, sort_order, created_at, updated_at, id
                )
                SELECT :space_id, :farm_id, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM task_space_farms
                    WHERE space_id = :space_id
                      AND farm_id = :farm_id
                )
                """
            ),
            {
                "space_id": space_id,
                "farm_id": farm_id,
                "id": str(uuid.uuid4()),
            },
        )


def upgrade():
    op.create_table(
        "task_space_farms",
        sa.Column("space_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_task_space_farm_sort_non_negative"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["space_id"], ["task_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "farm_id", name="uq_task_space_farm"),
    )
    op.create_index(op.f("ix_task_space_farms_farm_id"), "task_space_farms", ["farm_id"], unique=False)
    op.create_index(op.f("ix_task_space_farms_space_id"), "task_space_farms", ["space_id"], unique=False)
    _backfill_primary_farms()


def downgrade():
    op.drop_index(op.f("ix_task_space_farms_space_id"), table_name="task_space_farms")
    op.drop_index(op.f("ix_task_space_farms_farm_id"), table_name="task_space_farms")
    op.drop_table("task_space_farms")
