"""Add paddock gates.

Revision ID: 0f4c2d8e9a31
Revises: f8a6b1c3d9e2
Create Date: 2026-06-12 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0f4c2d8e9a31"
down_revision = "f8a6b1c3d9e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paddock_gates",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_a_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_b_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=11, scale=8), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("shared_boundary_length_m", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "last_state_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("paddock_a_id < paddock_b_id", name="ck_paddock_gate_sorted_ids"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_paddock_gate_status"),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_paddock_gate_source"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["paddock_a_id"], ["paddocks.id"]),
        sa.ForeignKeyConstraint(["paddock_b_id"], ["paddocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "paddock_a_id", "paddock_b_id", name="uq_gate_paddock_pair"),
    )
    op.create_index(op.f("ix_paddock_gates_farm_id"), "paddock_gates", ["farm_id"], unique=False)
    op.create_index(
        op.f("ix_paddock_gates_paddock_a_id"),
        "paddock_gates",
        ["paddock_a_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_paddock_gates_paddock_b_id"),
        "paddock_gates",
        ["paddock_b_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_paddock_gates_paddock_b_id"), table_name="paddock_gates")
    op.drop_index(op.f("ix_paddock_gates_paddock_a_id"), table_name="paddock_gates")
    op.drop_index(op.f("ix_paddock_gates_farm_id"), table_name="paddock_gates")
    op.drop_table("paddock_gates")
