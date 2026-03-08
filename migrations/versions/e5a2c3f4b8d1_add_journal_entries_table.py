"""Add journal entries table for analytics journal manual notes.

Revision ID: e5a2c3f4b8d1
Revises: d4f91b2c7a6e
Create Date: 2026-03-06 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e5a2c3f4b8d1"
down_revision = "d4f91b2c7a6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "journal_entries",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_journal_entries_event_at"), "journal_entries", ["event_at"], unique=False)
    op.create_index(op.f("ix_journal_entries_farm_id"), "journal_entries", ["farm_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_journal_entries_farm_id"), table_name="journal_entries")
    op.drop_index(op.f("ix_journal_entries_event_at"), table_name="journal_entries")
    op.drop_table("journal_entries")
