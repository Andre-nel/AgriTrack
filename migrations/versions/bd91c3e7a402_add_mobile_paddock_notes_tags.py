"""Add paddock notes and tags for mobile field edits.

Revision ID: bd91c3e7a402
Revises: aa7c2d9e4f10
Create Date: 2026-05-22 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bd91c3e7a402"
down_revision = "aa7c2d9e4f10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("paddocks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("tags_csv", sa.String(length=500), nullable=False, server_default="")
        )

    with op.batch_alter_table("paddocks", schema=None) as batch_op:
        batch_op.alter_column("tags_csv", server_default=None)


def downgrade():
    with op.batch_alter_table("paddocks", schema=None) as batch_op:
        batch_op.drop_column("tags_csv")
        batch_op.drop_column("notes")
