"""Add paddock events table for paddock log notes.

Revision ID: f6a9c2d8e4b3
Revises: e1b4c9a7d3f2
Create Date: 2026-05-25 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f6a9c2d8e4b3"
down_revision = "e1b4c9a7d3f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paddock_events",
        sa.Column("paddock_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column(
            "event_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["paddock_id"], ["paddocks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_paddock_events_event_at"), "paddock_events", ["event_at"], unique=False)
    op.create_index(op.f("ix_paddock_events_farm_id"), "paddock_events", ["farm_id"], unique=False)
    op.create_index(op.f("ix_paddock_events_paddock_id"), "paddock_events", ["paddock_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_paddock_events_paddock_id"), table_name="paddock_events")
    op.drop_index(op.f("ix_paddock_events_farm_id"), table_name="paddock_events")
    op.drop_index(op.f("ix_paddock_events_event_at"), table_name="paddock_events")
    op.drop_table("paddock_events")
