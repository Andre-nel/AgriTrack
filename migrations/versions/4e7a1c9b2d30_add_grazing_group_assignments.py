"""Add grazing group assignment counts.

Revision ID: 4e7a1c9b2d30
Revises: 3c9e4f6a8b21
Create Date: 2026-06-22 21:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "4e7a1c9b2d30"
down_revision = "3c9e4f6a8b21"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "grazing_allocation_group_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("grazing_allocation_id", sa.String(length=36), nullable=False),
        sa.Column("animal_group_type_id", sa.String(length=36), nullable=False),
        sa.Column("head_count", sa.Integer(), nullable=False),
        sa.Column("group_fraction", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("assigned_lsu", sa.Numeric(precision=12, scale=4), nullable=False),
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
        sa.CheckConstraint("head_count > 0", name="ck_grazing_group_assignment_head_count_positive"),
        sa.CheckConstraint(
            "group_fraction > 0 and group_fraction <= 1",
            name="ck_grazing_group_assignment_fraction",
        ),
        sa.CheckConstraint(
            "assigned_lsu >= 0",
            name="ck_grazing_group_assignment_lsu_non_negative",
        ),
        sa.ForeignKeyConstraint(["animal_group_type_id"], ["animal_group_types.id"]),
        sa.ForeignKeyConstraint(["grazing_allocation_id"], ["grazing_allocations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "grazing_allocation_id",
            "animal_group_type_id",
            name="uq_grazing_group_assignment_allocation_group",
        ),
    )
    op.create_index(
        op.f("ix_grazing_allocation_group_assignments_grazing_allocation_id"),
        "grazing_allocation_group_assignments",
        ["grazing_allocation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_grazing_allocation_group_assignments_animal_group_type_id"),
        "grazing_allocation_group_assignments",
        ["animal_group_type_id"],
        unique=False,
    )

    with op.batch_alter_table("grazing_allocation_lsu_breakdown_history", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allocated_head_count",
                sa.Numeric(precision=12, scale=4),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        """
        UPDATE grazing_allocation_lsu_breakdown_history
        SET allocated_head_count = head_count * allocation_fraction
        """
    )

    with op.batch_alter_table("grazing_allocation_lsu_breakdown_history", schema=None) as batch_op:
        batch_op.alter_column("allocated_head_count", server_default=None)


def downgrade():
    with op.batch_alter_table("grazing_allocation_lsu_breakdown_history", schema=None) as batch_op:
        batch_op.drop_column("allocated_head_count")

    op.drop_index(
        op.f("ix_grazing_allocation_group_assignments_animal_group_type_id"),
        table_name="grazing_allocation_group_assignments",
    )
    op.drop_index(
        op.f("ix_grazing_allocation_group_assignments_grazing_allocation_id"),
        table_name="grazing_allocation_group_assignments",
    )
    op.drop_table("grazing_allocation_group_assignments")
