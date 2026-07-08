"""Add simulator module tables.

Revision ID: bf4a7d9c2e31
Revises: ab1c2d3e4f56
Create Date: 2026-07-05 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "bf4a7d9c2e31"
down_revision = "ab1c2d3e4f56"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "simulator_scenarios",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("projection_year", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("income_inflation_rate", sa.Numeric(precision=7, scale=4), nullable=False, server_default="0"),
        sa.Column("expense_inflation_rate", sa.Numeric(precision=7, scale=4), nullable=False, server_default="0"),
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
            "projection_year BETWEEN 2000 AND 2100",
            name="ck_simulator_scenarios_projection_year_range",
        ),
        sa.CheckConstraint(
            "income_inflation_rate >= 0",
            name="ck_simulator_scenarios_income_inflation_non_negative",
        ),
        sa.CheckConstraint(
            "expense_inflation_rate >= 0",
            name="ck_simulator_scenarios_expense_inflation_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "simulator_revenue_assumptions",
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("species", sa.String(length=20), nullable=False),
        sa.Column("breed", sa.String(length=50), nullable=False),
        sa.Column("yearly_revenue_per_adult_female", sa.Numeric(precision=12, scale=2), nullable=False),
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
            name="ck_simulator_revenue_species_allowed",
        ),
        sa.CheckConstraint(
            "yearly_revenue_per_adult_female > 0",
            name="ck_simulator_revenue_amount_positive",
        ),
        sa.ForeignKeyConstraint(["scenario_id"], ["simulator_scenarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scenario_id",
            "species",
            "breed",
            name="uq_simulator_revenue_scenario_species_breed",
        ),
    )
    op.create_index(
        op.f("ix_simulator_revenue_assumptions_scenario_id"),
        "simulator_revenue_assumptions",
        ["scenario_id"],
        unique=False,
    )

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


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "simulator_expenses" in table_names:
        op.drop_index(op.f("ix_simulator_expenses_scenario_id"), table_name="simulator_expenses")
        op.drop_index(op.f("ix_simulator_expenses_farm_id"), table_name="simulator_expenses")
        op.drop_table("simulator_expenses")
    if "simulator_stock_details" in table_names:
        op.drop_index(
            op.f("ix_simulator_stock_details_scenario_id"),
            table_name="simulator_stock_details",
        )
        op.drop_index(
            op.f("ix_simulator_stock_details_farm_id"),
            table_name="simulator_stock_details",
        )
        op.drop_table("simulator_stock_details")
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
    if "simulator_revenue_assumptions" in table_names:
        op.drop_index(
            op.f("ix_simulator_revenue_assumptions_scenario_id"),
            table_name="simulator_revenue_assumptions",
        )
        op.drop_table("simulator_revenue_assumptions")
    if "simulator_scenarios" in table_names:
        op.drop_table("simulator_scenarios")
