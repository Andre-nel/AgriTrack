"""Allow orchard row and column labels to be positioned independently.

Revision ID: f9b2d4e6a810
Revises: e8a1c3d5f709
"""

from alembic import op
import sqlalchemy as sa

revision = "f9b2d4e6a810"
down_revision = "e8a1c3d5f709"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orchard_rows", sa.Column("label_x_m", sa.Float(), nullable=True))
    op.add_column("orchard_rows", sa.Column("label_y_m", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("orchard_rows", "label_y_m")
    op.drop_column("orchard_rows", "label_x_m")
