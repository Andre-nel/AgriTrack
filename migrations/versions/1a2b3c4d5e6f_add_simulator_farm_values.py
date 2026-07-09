"""Add simulator farm values.

Revision ID: 1a2b3c4d5e6f
Revises: 0d9c7e6a5b12
Create Date: 2026-07-08 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = "0d9c7e6a5b12"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    table_names = _table_names()
    if "_alembic_tmp_simulator_farms" in table_names:
        op.drop_table("_alembic_tmp_simulator_farms")

    columns = _column_names("simulator_farms")
    if "initial_farm_value" not in columns:
        op.add_column(
            "simulator_farms",
            sa.Column(
                "initial_farm_value",
                sa.Numeric(precision=12, scale=2),
                nullable=False,
                server_default="0",
            )
        )
    if "farm_value_inflation_rate" not in columns:
        op.add_column(
            "simulator_farms",
            sa.Column(
                "farm_value_inflation_rate",
                sa.Numeric(precision=7, scale=4),
                nullable=False,
                server_default="0",
            )
        )


def downgrade():
    columns = _column_names("simulator_farms")
    if "farm_value_inflation_rate" in columns:
        op.drop_column("simulator_farms", "farm_value_inflation_rate")
    if "initial_farm_value" in columns:
        op.drop_column("simulator_farms", "initial_farm_value")
