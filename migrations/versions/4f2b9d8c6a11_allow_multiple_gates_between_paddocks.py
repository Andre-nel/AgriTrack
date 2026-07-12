"""Allow multiple gates between the same paddocks.

Revision ID: 4f2b9d8c6a11
Revises: 3d7e9f1a4c82
Create Date: 2026-07-12 09:00:00.000000
"""

from alembic import op


revision = "4f2b9d8c6a11"
down_revision = "3d7e9f1a4c82"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("paddock_gates", schema=None, recreate="always") as batch_op:
            batch_op.drop_constraint("uq_gate_paddock_pair", type_="unique")
        return
    op.drop_constraint("uq_gate_paddock_pair", "paddock_gates", type_="unique")


def downgrade():
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("paddock_gates", schema=None, recreate="always") as batch_op:
            batch_op.create_unique_constraint(
                "uq_gate_paddock_pair",
                ["farm_id", "paddock_a_id", "paddock_b_id"],
            )
        return
    op.create_unique_constraint(
        "uq_gate_paddock_pair",
        "paddock_gates",
        ["farm_id", "paddock_a_id", "paddock_b_id"],
    )
