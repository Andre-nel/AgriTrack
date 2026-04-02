"""Allow reusing pump assets across water connections

Revision ID: 3a5c8d1e7f0b
Revises: 1f3d9c4b7e2a
Create Date: 2026-03-18 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "3a5c8d1e7f0b"
down_revision = "1f3d9c4b7e2a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("water_connections") as batch_op:
        batch_op.drop_index("ix_water_connections_pump_asset_id")
        batch_op.create_index("ix_water_connections_pump_asset_id", ["pump_asset_id"], unique=False)


def downgrade():
    with op.batch_alter_table("water_connections") as batch_op:
        batch_op.drop_index("ix_water_connections_pump_asset_id")
        batch_op.create_index("ix_water_connections_pump_asset_id", ["pump_asset_id"], unique=True)
