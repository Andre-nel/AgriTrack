"""Repair simulator schema after early V1 revisions.

Revision ID: c9e1a4b7d6f2
Revises: bf4a7d9c2e31
Create Date: 2026-07-06 18:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9e1a4b7d6f2"
down_revision = "bf4a7d9c2e31"
branch_labels = None
depends_on = None


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _create_stock_details_table() -> None:
    op.create_table(
        "simulator_stock_details",
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("species", sa.String(length=20), nullable=False),
        sa.Column("breed", sa.String(length=50), nullable=False),
        sa.Column("adult_female_count", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')",
            name="ck_simulator_stock_detail_species_allowed",
        ),
        sa.CheckConstraint(
            "adult_female_count >= 0",
            name="ck_simulator_stock_detail_count_non_negative",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["scenario_id"], ["simulator_scenarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_id",
            "farm_id",
            "species",
            "breed",
            name="uq_simulator_stock_detail_scenario_farm_species_breed",
        ),
    )
    op.create_index(
        op.f("ix_simulator_stock_details_farm_id"),
        "simulator_stock_details",
        ["farm_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_simulator_stock_details_scenario_id"),
        "simulator_stock_details",
        ["scenario_id"],
        unique=False,
    )


def _create_expenses_table() -> None:
    op.create_table(
        "simulator_expenses",
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=True),
        sa.Column("category_code", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("expense_type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("recurrence", sa.String(length=20), nullable=True),
        sa.Column("start_month", sa.Integer(), nullable=True),
        sa.Column("loan_principal", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("annual_interest_rate", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("remaining_term_years", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("payment_interval_months", sa.Integer(), nullable=True),
        sa.Column("first_payment_month", sa.Integer(), nullable=True),
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
        sa.CheckConstraint(
            "expense_type IN ('standard', 'loan')",
            name="ck_simulator_expense_type_allowed",
        ),
        sa.CheckConstraint(
            "recurrence IS NULL OR recurrence IN ('monthly', 'yearly', 'once_off')",
            name="ck_simulator_expense_recurrence_allowed",
        ),
        sa.CheckConstraint(
            "start_month IS NULL OR start_month BETWEEN 1 AND 12",
            name="ck_simulator_expense_start_month_range",
        ),
        sa.CheckConstraint(
            "first_payment_month IS NULL OR first_payment_month BETWEEN 1 AND 12",
            name="ck_simulator_expense_first_payment_month_range",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount > 0",
            name="ck_simulator_expense_amount_positive",
        ),
        sa.CheckConstraint(
            "loan_principal IS NULL OR loan_principal > 0",
            name="ck_simulator_expense_loan_principal_positive",
        ),
        sa.CheckConstraint(
            "annual_interest_rate IS NULL OR annual_interest_rate >= 0",
            name="ck_simulator_expense_interest_non_negative",
        ),
        sa.CheckConstraint(
            "remaining_term_years IS NULL OR remaining_term_years > 0",
            name="ck_simulator_expense_term_positive",
        ),
        sa.CheckConstraint(
            "payment_interval_months IS NULL OR payment_interval_months > 0",
            name="ck_simulator_expense_interval_positive",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["scenario_id"], ["simulator_scenarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simulator_expenses_farm_id"),
        "simulator_expenses",
        ["farm_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_simulator_expenses_scenario_id"),
        "simulator_expenses",
        ["scenario_id"],
        unique=False,
    )


def _seed_stock_details_from_live(bind) -> None:
    scenario_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM simulator_scenarios")).fetchall()]
    if not scenario_ids:
        return

    live_rows = bind.execute(
        sa.text(
            """
            SELECT
                mobs.farm_id,
                animal_group_types.species,
                animal_group_types.breed,
                SUM(animal_group_balances.head_count) AS head_count
            FROM animal_group_balances
            JOIN mobs ON animal_group_balances.mob_id = mobs.id
            JOIN animal_group_types ON animal_group_balances.animal_group_type_id = animal_group_types.id
            JOIN farms ON mobs.farm_id = farms.id
            WHERE farms.active = 1
              AND mobs.status = 'active'
              AND animal_group_types.age_class = 'adult'
              AND (
                (animal_group_types.species = 'Cattle' AND animal_group_types.sex = 'cow')
                OR (animal_group_types.species = 'Sheep' AND animal_group_types.sex = 'ewe')
                OR (animal_group_types.species = 'Goat' AND animal_group_types.sex = 'ewe')
              )
            GROUP BY mobs.farm_id, animal_group_types.species, animal_group_types.breed
            """
        )
    ).fetchall()

    old_adjustments = {}
    if "simulator_stock_adjustments" in _table_names(bind):
        for row in bind.execute(
            sa.text(
                """
                SELECT scenario_id, farm_id, species, breed, adjustment_count
                FROM simulator_stock_adjustments
                """
            )
        ).fetchall():
            old_adjustments[(row[0], row[1], row[2], row[3])] = int(row[4] or 0)

    for scenario_id in scenario_ids:
        for farm_id, species, breed, head_count in live_rows:
            adjustment = old_adjustments.pop((scenario_id, farm_id, species, breed), 0)
            adult_female_count = max(0, int(head_count or 0) + adjustment)
            bind.execute(
                sa.text(
                    """
                    INSERT INTO simulator_stock_details (
                        scenario_id, farm_id, species, breed, adult_female_count, id
                    )
                    VALUES (
                        :scenario_id, :farm_id, :species, :breed, :adult_female_count,
                        lower(hex(randomblob(4))) || '-' ||
                        lower(hex(randomblob(2))) || '-' ||
                        lower(hex(randomblob(2))) || '-' ||
                        lower(hex(randomblob(2))) || '-' ||
                        lower(hex(randomblob(6)))
                    )
                    """
                ),
                {
                    "scenario_id": scenario_id,
                    "farm_id": farm_id,
                    "species": species,
                    "breed": breed,
                    "adult_female_count": adult_female_count,
                },
            )

    for (scenario_id, farm_id, species, breed), adjustment in old_adjustments.items():
        bind.execute(
            sa.text(
                """
                INSERT INTO simulator_stock_details (
                    scenario_id, farm_id, species, breed, adult_female_count, id
                )
                VALUES (
                    :scenario_id, :farm_id, :species, :breed, :adult_female_count,
                    lower(hex(randomblob(4))) || '-' ||
                    lower(hex(randomblob(2))) || '-' ||
                    lower(hex(randomblob(2))) || '-' ||
                    lower(hex(randomblob(2))) || '-' ||
                    lower(hex(randomblob(6)))
                )
                """
            ),
            {
                "scenario_id": scenario_id,
                "farm_id": farm_id,
                "species": species,
                "breed": breed,
                "adult_female_count": max(0, adjustment),
            },
        )


def upgrade():
    bind = op.get_bind()
    table_names = _table_names(bind)

    if "simulator_stock_details" not in table_names:
        _create_stock_details_table()
        _seed_stock_details_from_live(bind)
        table_names = _table_names(bind)

    if "simulator_expenses" not in table_names:
        _create_expenses_table()
        table_names = _table_names(bind)

    if "simulator_stock_adjustments" in table_names:
        op.drop_index(
            op.f("ix_simulator_stock_adjustments_scenario_id"),
            table_name="simulator_stock_adjustments",
        )
        op.drop_index(
            op.f("ix_simulator_stock_adjustments_farm_id"),
            table_name="simulator_stock_adjustments",
        )
        op.drop_table("simulator_stock_adjustments")


def downgrade():
    # Keep this repair migration reversible at the version-stamp level without
    # tearing down repaired tables. The base simulator migration owns table drops.
    pass
