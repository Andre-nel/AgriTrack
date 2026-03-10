"""Add calendar activity tables.

Revision ID: b6d7e8f9a1c2
Revises: a8f3c6d2e4b1
Create Date: 2026-03-10 10:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b6d7e8f9a1c2"
down_revision = "a8f3c6d2e4b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "calendar_activities",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("repeat_interval", sa.Integer(), nullable=True),
        sa.Column("repeat_unit", sa.String(length=10), nullable=True),
        sa.Column("repeat_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            (
                "(repeat_interval IS NULL AND repeat_unit IS NULL AND repeat_until IS NULL) "
                "OR (repeat_interval IS NOT NULL AND repeat_unit IS NOT NULL)"
            ),
            name="ck_calendar_activities_repeat_fields",
        ),
        sa.CheckConstraint(
            "repeat_interval IS NULL OR repeat_interval > 0",
            name="ck_calendar_activities_repeat_interval_positive",
        ),
        sa.CheckConstraint(
            "repeat_unit IS NULL OR repeat_unit IN ('days', 'weeks', 'months', 'years')",
            name="ck_calendar_activities_repeat_unit_valid",
        ),
        sa.CheckConstraint(
            "repeat_until IS NULL OR repeat_until >= start_date",
            name="ck_calendar_activities_repeat_until_after_start",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_calendar_activities_farm_id"), "calendar_activities", ["farm_id"], unique=False)
    op.create_index(op.f("ix_calendar_activities_start_date"), "calendar_activities", ["start_date"], unique=False)
    op.create_index(op.f("ix_calendar_activities_repeat_until"), "calendar_activities", ["repeat_until"], unique=False)

    op.create_table(
        "calendar_activity_exceptions",
        sa.Column("calendar_activity_id", sa.String(length=36), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("rescheduled_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "action IN ('skip', 'move')",
            name="ck_calendar_activity_exceptions_action_valid",
        ),
        sa.CheckConstraint(
            (
                "(action = 'skip' AND rescheduled_date IS NULL) "
                "OR (action = 'move' AND rescheduled_date IS NOT NULL)"
            ),
            name="ck_calendar_activity_exceptions_reschedule_pair",
        ),
        sa.ForeignKeyConstraint(["calendar_activity_id"], ["calendar_activities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calendar_activity_id",
            "occurrence_date",
            name="uq_calendar_activity_exceptions_activity_occurrence",
        ),
    )
    op.create_index(
        op.f("ix_calendar_activity_exceptions_calendar_activity_id"),
        "calendar_activity_exceptions",
        ["calendar_activity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_activity_exceptions_occurrence_date"),
        "calendar_activity_exceptions",
        ["occurrence_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_activity_exceptions_rescheduled_date"),
        "calendar_activity_exceptions",
        ["rescheduled_date"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_calendar_activity_exceptions_rescheduled_date"),
        table_name="calendar_activity_exceptions",
    )
    op.drop_index(
        op.f("ix_calendar_activity_exceptions_occurrence_date"),
        table_name="calendar_activity_exceptions",
    )
    op.drop_index(
        op.f("ix_calendar_activity_exceptions_calendar_activity_id"),
        table_name="calendar_activity_exceptions",
    )
    op.drop_table("calendar_activity_exceptions")

    op.drop_index(op.f("ix_calendar_activities_repeat_until"), table_name="calendar_activities")
    op.drop_index(op.f("ix_calendar_activities_start_date"), table_name="calendar_activities")
    op.drop_index(op.f("ix_calendar_activities_farm_id"), table_name="calendar_activities")
    op.drop_table("calendar_activities")
