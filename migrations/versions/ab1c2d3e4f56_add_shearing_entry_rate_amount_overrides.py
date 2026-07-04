"""Add shearing entry rate and amount overrides.

Revision ID: ab1c2d3e4f56
Revises: 95d6b4c8a2f1
Create Date: 2026-07-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "ab1c2d3e4f56"
down_revision = "95d6b4c8a2f1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shearing_entries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("unit_rate_override", sa.Numeric(precision=10, scale=2), nullable=True))
        batch_op.add_column(sa.Column("line_amount_override", sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.create_check_constraint(
            "ck_shearing_entry_rate_override_non_negative",
            "unit_rate_override IS NULL OR unit_rate_override >= 0",
        )
        batch_op.create_check_constraint(
            "ck_shearing_entry_amount_override_non_negative",
            "line_amount_override IS NULL OR line_amount_override >= 0",
        )


def downgrade():
    with op.batch_alter_table("shearing_entries", schema=None) as batch_op:
        batch_op.drop_constraint("ck_shearing_entry_amount_override_non_negative", type_="check")
        batch_op.drop_constraint("ck_shearing_entry_rate_override_non_negative", type_="check")
        batch_op.drop_column("line_amount_override")
        batch_op.drop_column("unit_rate_override")
