"""Add incidents table.

Revision ID: 2c4f6a8b0d1e
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-08 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2c4f6a8b0d1e"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incidents",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("reported_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_incidents_farm_id"), "incidents", ["farm_id"], unique=False)
    op.create_index(op.f("ix_incidents_occurred_on"), "incidents", ["occurred_on"], unique=False)
    op.create_index(op.f("ix_incidents_category"), "incidents", ["category"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_incidents_category"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_occurred_on"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_farm_id"), table_name="incidents")
    op.drop_table("incidents")

