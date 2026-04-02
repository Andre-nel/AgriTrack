"""Add weir size to water assets.

Revision ID: 1f3d9c4b7e2a
Revises: 9b4c6d8e1f2a
Create Date: 2026-03-17 13:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f3d9c4b7e2a"
down_revision = "9b4c6d8e1f2a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("water_assets") as batch_op:
        batch_op.add_column(sa.Column("weir_size", sa.String(length=20), nullable=True))
        batch_op.create_check_constraint(
            "ck_water_asset_weir_size",
            "weir_size IS NULL OR weir_size IN ('small', 'medium', 'large')",
        )


def downgrade():
    with op.batch_alter_table("water_assets") as batch_op:
        batch_op.drop_constraint("ck_water_asset_weir_size", type_="check")
        batch_op.drop_column("weir_size")
