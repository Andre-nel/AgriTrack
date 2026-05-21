"""Add task entity links.

Revision ID: aa7c2d9e4f10
Revises: 7a2d9f4c1b6e
Create Date: 2026-05-21 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "aa7c2d9e4f10"
down_revision = "7a2d9f4c1b6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_entity_links",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_id", sa.String(length=36), nullable=True),
        sa.Column("water_asset_id", sa.String(length=36), nullable=True),
        sa.Column("mob_id", sa.String(length=36), nullable=True),
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
        sa.CheckConstraint(
            "(paddock_id IS NOT NULL AND water_asset_id IS NULL AND mob_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NOT NULL AND mob_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NULL AND mob_id IS NOT NULL)",
            name="ck_task_entity_links_one_entity",
        ),
        sa.ForeignKeyConstraint(["mob_id"], ["mobs.id"]),
        sa.ForeignKeyConstraint(["paddock_id"], ["paddocks.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["water_asset_id"], ["water_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "mob_id", name="uq_task_entity_links_task_mob"),
        sa.UniqueConstraint("task_id", "paddock_id", name="uq_task_entity_links_task_paddock"),
        sa.UniqueConstraint(
            "task_id",
            "water_asset_id",
            name="uq_task_entity_links_task_water_asset",
        ),
    )
    op.create_index(op.f("ix_task_entity_links_mob_id"), "task_entity_links", ["mob_id"], unique=False)
    op.create_index(
        op.f("ix_task_entity_links_paddock_id"),
        "task_entity_links",
        ["paddock_id"],
        unique=False,
    )
    op.create_index(op.f("ix_task_entity_links_task_id"), "task_entity_links", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_task_entity_links_water_asset_id"),
        "task_entity_links",
        ["water_asset_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_task_entity_links_water_asset_id"), table_name="task_entity_links")
    op.drop_index(op.f("ix_task_entity_links_task_id"), table_name="task_entity_links")
    op.drop_index(op.f("ix_task_entity_links_paddock_id"), table_name="task_entity_links")
    op.drop_index(op.f("ix_task_entity_links_mob_id"), table_name="task_entity_links")
    op.drop_table("task_entity_links")
