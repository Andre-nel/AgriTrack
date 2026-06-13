"""Add fence section tags.

Revision ID: 3c9e4f6a8b21
Revises: 2b8f6d4e9c10
Create Date: 2026-06-13 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "3c9e4f6a8b21"
down_revision = "2b8f6d4e9c10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("fence_sections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("tags_csv", sa.String(length=500), nullable=False, server_default="")
        )

    with op.batch_alter_table("fence_sections", schema=None) as batch_op:
        batch_op.alter_column("tags_csv", server_default=None)


def downgrade():
    with op.batch_alter_table("fence_sections", schema=None) as batch_op:
        batch_op.drop_column("tags_csv")
