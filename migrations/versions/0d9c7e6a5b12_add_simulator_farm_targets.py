"""Add simulator farm targets.

Revision ID: 0d9c7e6a5b12
Revises: d3f8b6a9c1e4
Create Date: 2026-07-08 12:00:00.000000
"""

import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0d9c7e6a5b12"
down_revision = "d3f8b6a9c1e4"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _seed_existing_scenario_farms() -> None:
    bind = op.get_bind()
    scenarios = bind.execute(sa.text("SELECT id FROM simulator_scenarios")).fetchall()
    farms = bind.execute(sa.text("SELECT id, name FROM farms WHERE active = 1")).fetchall()
    existing = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text("SELECT scenario_id, farm_id FROM simulator_farms WHERE farm_id IS NOT NULL")
        ).fetchall()
    }

    for scenario_id, in scenarios:
        for farm_id, farm_name in farms:
            if (scenario_id, farm_id) in existing:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO simulator_farms (
                        scenario_id, farm_id, name, created_at, updated_at, id
                    )
                    VALUES (
                        :scenario_id, :farm_id, :name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :id
                    )
                    """
                ),
                {
                    "scenario_id": scenario_id,
                    "farm_id": farm_id,
                    "name": farm_name,
                    "id": str(uuid.uuid4()),
                },
            )


def _backfill_target_links() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE simulator_stock_details
            SET simulator_farm_id = (
                SELECT simulator_farms.id
                FROM simulator_farms
                WHERE simulator_farms.scenario_id = simulator_stock_details.scenario_id
                  AND simulator_farms.farm_id = simulator_stock_details.farm_id
                LIMIT 1
            )
            WHERE simulator_farm_id IS NULL
              AND farm_id IS NOT NULL
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE simulator_expenses
            SET simulator_farm_id = (
                SELECT simulator_farms.id
                FROM simulator_farms
                WHERE simulator_farms.scenario_id = simulator_expenses.scenario_id
                  AND simulator_farms.farm_id = simulator_expenses.farm_id
                LIMIT 1
            )
            WHERE simulator_farm_id IS NULL
              AND farm_id IS NOT NULL
            """
        )
    )


def upgrade():
    table_names = _table_names()
    if "simulator_farms" not in table_names:
        op.create_table(
            "simulator_farms",
            sa.Column("scenario_id", sa.String(length=36), nullable=False),
            sa.Column("farm_id", sa.String(length=36), nullable=True),
            sa.Column("name", sa.String(length=120), nullable=False),
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
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
            sa.ForeignKeyConstraint(["scenario_id"], ["simulator_scenarios.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("scenario_id", "farm_id", name="uq_simulator_farm_scenario_farm"),
            sa.UniqueConstraint("scenario_id", "name", name="uq_simulator_farm_scenario_name"),
        )
        op.create_index(op.f("ix_simulator_farms_farm_id"), "simulator_farms", ["farm_id"], unique=False)
        op.create_index(
            op.f("ix_simulator_farms_scenario_id"),
            "simulator_farms",
            ["scenario_id"],
            unique=False,
        )

    stock_columns = _column_names("simulator_stock_details")
    with op.batch_alter_table("simulator_stock_details", schema=None) as batch_op:
        if "simulator_farm_id" not in stock_columns:
            batch_op.add_column(sa.Column("simulator_farm_id", sa.String(length=36), nullable=True))
            batch_op.create_index(
                op.f("ix_simulator_stock_details_simulator_farm_id"),
                ["simulator_farm_id"],
                unique=False,
            )
            batch_op.create_foreign_key(
                "fk_simulator_stock_details_simulator_farm_id",
                "simulator_farms",
                ["simulator_farm_id"],
                ["id"],
            )
            batch_op.create_unique_constraint(
                "uq_simulator_stock_detail_scenario_target_species_breed",
                ["scenario_id", "simulator_farm_id", "species", "breed"],
            )
        batch_op.alter_column("farm_id", existing_type=sa.String(length=36), nullable=True)

    expense_columns = _column_names("simulator_expenses")
    with op.batch_alter_table("simulator_expenses", schema=None) as batch_op:
        if "simulator_farm_id" not in expense_columns:
            batch_op.add_column(sa.Column("simulator_farm_id", sa.String(length=36), nullable=True))
            batch_op.create_index(
                op.f("ix_simulator_expenses_simulator_farm_id"),
                ["simulator_farm_id"],
                unique=False,
            )
            batch_op.create_foreign_key(
                "fk_simulator_expenses_simulator_farm_id",
                "simulator_farms",
                ["simulator_farm_id"],
                ["id"],
            )

    _seed_existing_scenario_farms()
    _backfill_target_links()


def downgrade():
    bind = op.get_bind()
    if "simulator_stock_details" in _table_names():
        bind.execute(sa.text("DELETE FROM simulator_stock_details WHERE farm_id IS NULL"))
        with op.batch_alter_table("simulator_stock_details", schema=None) as batch_op:
            batch_op.drop_constraint(
                "uq_simulator_stock_detail_scenario_target_species_breed",
                type_="unique",
            )
            batch_op.drop_constraint("fk_simulator_stock_details_simulator_farm_id", type_="foreignkey")
            batch_op.drop_index(op.f("ix_simulator_stock_details_simulator_farm_id"))
            batch_op.drop_column("simulator_farm_id")
            batch_op.alter_column("farm_id", existing_type=sa.String(length=36), nullable=False)

    if "simulator_expenses" in _table_names():
        with op.batch_alter_table("simulator_expenses", schema=None) as batch_op:
            batch_op.drop_constraint("fk_simulator_expenses_simulator_farm_id", type_="foreignkey")
            batch_op.drop_index(op.f("ix_simulator_expenses_simulator_farm_id"))
            batch_op.drop_column("simulator_farm_id")

    if "simulator_farms" in _table_names():
        op.drop_index(op.f("ix_simulator_farms_scenario_id"), table_name="simulator_farms")
        op.drop_index(op.f("ix_simulator_farms_farm_id"), table_name="simulator_farms")
        op.drop_table("simulator_farms")
