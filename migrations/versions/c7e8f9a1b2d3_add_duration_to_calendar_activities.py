"""Add duration to calendar activities.

Revision ID: c7e8f9a1b2d3
Revises: b6d7e8f9a1c2
Create Date: 2026-03-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7e8f9a1b2d3"
down_revision = "b6d7e8f9a1c2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("calendar_activities") as batch_op:
        batch_op.add_column(
            sa.Column(
                "duration_days",
                sa.Numeric(precision=6, scale=2),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.create_check_constraint(
            "ck_calendar_activities_duration_positive",
            "duration_days > 0",
        )


def downgrade():
    with op.batch_alter_table("calendar_activities") as batch_op:
        batch_op.drop_constraint("ck_calendar_activities_duration_positive", type_="check")
        batch_op.drop_column("duration_days")
