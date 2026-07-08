"""Add simulator inflation rates.

Revision ID: d3f8b6a9c1e4
Revises: c9e1a4b7d6f2
Create Date: 2026-07-06 19:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3f8b6a9c1e4"
down_revision = "c9e1a4b7d6f2"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade():
    columns = _column_names("simulator_scenarios")
    if "income_inflation_rate" not in columns:
        op.add_column(
            "simulator_scenarios",
            sa.Column(
                "income_inflation_rate",
                sa.Numeric(precision=7, scale=4),
                nullable=False,
                server_default="0",
            ),
        )
    if "expense_inflation_rate" not in columns:
        op.add_column(
            "simulator_scenarios",
            sa.Column(
                "expense_inflation_rate",
                sa.Numeric(precision=7, scale=4),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade():
    columns = _column_names("simulator_scenarios")
    if "expense_inflation_rate" in columns:
        op.drop_column("simulator_scenarios", "expense_inflation_rate")
    if "income_inflation_rate" in columns:
        op.drop_column("simulator_scenarios", "income_inflation_rate")
