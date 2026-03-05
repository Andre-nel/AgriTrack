"""Add mob events table for mob activity history.

Revision ID: d4f91b2c7a6e
Revises: c2a7d9b41f6e
Create Date: 2026-03-05 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4f91b2c7a6e"
down_revision = "c2a7d9b41f6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mob_events",
        sa.Column("mob_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["mob_id"], ["mobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mob_events_event_at"), "mob_events", ["event_at"], unique=False)
    op.create_index(op.f("ix_mob_events_farm_id"), "mob_events", ["farm_id"], unique=False)
    op.create_index(op.f("ix_mob_events_mob_id"), "mob_events", ["mob_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_mob_events_mob_id"), table_name="mob_events")
    op.drop_index(op.f("ix_mob_events_farm_id"), table_name="mob_events")
    op.drop_index(op.f("ix_mob_events_event_at"), table_name="mob_events")
    op.drop_table("mob_events")
