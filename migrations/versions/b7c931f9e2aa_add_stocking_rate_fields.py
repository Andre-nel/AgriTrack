"""Add farm and paddock stocking rate fields.

Revision ID: b7c931f9e2aa
Revises: 8b2d4f31c6aa
Create Date: 2026-02-27 16:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7c931f9e2aa"
down_revision = "8b2d4f31c6aa"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("farms", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "default_stocking_rate_ha_per_lsu",
                sa.Numeric(8, 2),
                nullable=False,
                server_default=sa.text("6"),
            )
        )
        batch_op.create_check_constraint(
            "ck_farm_stocking_rate_positive",
            "default_stocking_rate_ha_per_lsu > 0",
        )

    with op.batch_alter_table("paddocks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stocking_rate_ha_per_lsu_override", sa.Numeric(8, 2), nullable=True))
        batch_op.create_check_constraint(
            "ck_paddock_stocking_rate_positive",
            "stocking_rate_ha_per_lsu_override IS NULL OR stocking_rate_ha_per_lsu_override > 0",
        )


def downgrade():
    with op.batch_alter_table("paddocks", schema=None) as batch_op:
        batch_op.drop_constraint("ck_paddock_stocking_rate_positive", type_="check")
        batch_op.drop_column("stocking_rate_ha_per_lsu_override")

    with op.batch_alter_table("farms", schema=None) as batch_op:
        batch_op.drop_constraint("ck_farm_stocking_rate_positive", type_="check")
        batch_op.drop_column("default_stocking_rate_ha_per_lsu")
