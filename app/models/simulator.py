from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SimulatorScenario(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "simulator_scenarios"

    name = db.Column(db.String(120), nullable=False)
    projection_year = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    income_inflation_rate = db.Column(db.Numeric(7, 4), nullable=False, default=0)
    expense_inflation_rate = db.Column(db.Numeric(7, 4), nullable=False, default=0)

    revenue_assumptions = db.relationship(
        "SimulatorRevenueAssumption",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )
    stock_details = db.relationship(
        "SimulatorStockDetail",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )
    farm_targets = db.relationship(
        "SimulatorFarm",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )
    expenses = db.relationship(
        "SimulatorExpense",
        back_populates="scenario",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint(
            "projection_year BETWEEN 2000 AND 2100",
            name="ck_simulator_scenarios_projection_year_range",
        ),
        db.CheckConstraint(
            "income_inflation_rate >= 0",
            name="ck_simulator_scenarios_income_inflation_non_negative",
        ),
        db.CheckConstraint(
            "expense_inflation_rate >= 0",
            name="ck_simulator_scenarios_expense_inflation_non_negative",
        ),
    )


class SimulatorFarm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "simulator_farms"

    scenario_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_scenarios.id"),
        nullable=False,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    initial_farm_value = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    farm_value_inflation_rate = db.Column(db.Numeric(7, 4), nullable=False, default=0)

    scenario = db.relationship("SimulatorScenario", back_populates="farm_targets")
    farm = db.relationship("Farm")
    stock_details = db.relationship("SimulatorStockDetail", back_populates="simulator_farm")
    expenses = db.relationship("SimulatorExpense", back_populates="simulator_farm")

    @property
    def display_name(self) -> str:
        return self.farm.name if self.farm else self.name

    __table_args__ = (
        db.UniqueConstraint(
            "scenario_id",
            "farm_id",
            name="uq_simulator_farm_scenario_farm",
        ),
        db.UniqueConstraint(
            "scenario_id",
            "name",
            name="uq_simulator_farm_scenario_name",
        ),
        db.CheckConstraint(
            "initial_farm_value >= 0",
            name="ck_simulator_farm_initial_value_non_negative",
        ),
        db.CheckConstraint(
            "farm_value_inflation_rate >= 0",
            name="ck_simulator_farm_value_inflation_non_negative",
        ),
    )


class SimulatorRevenueAssumption(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "simulator_revenue_assumptions"

    scenario_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_scenarios.id"),
        nullable=False,
        index=True,
    )
    species = db.Column(db.String(20), nullable=False)
    breed = db.Column(db.String(50), nullable=False)
    yearly_revenue_per_adult_female = db.Column(db.Numeric(12, 2), nullable=False)

    scenario = db.relationship("SimulatorScenario", back_populates="revenue_assumptions")

    __table_args__ = (
        db.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')",
            name="ck_simulator_revenue_species_allowed",
        ),
        db.CheckConstraint(
            "yearly_revenue_per_adult_female > 0",
            name="ck_simulator_revenue_amount_positive",
        ),
        db.UniqueConstraint(
            "scenario_id",
            "species",
            "breed",
            name="uq_simulator_revenue_scenario_species_breed",
        ),
    )


class SimulatorStockDetail(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "simulator_stock_details"

    scenario_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_scenarios.id"),
        nullable=False,
        index=True,
    )
    simulator_farm_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_farms.id"),
        nullable=True,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=True, index=True)
    species = db.Column(db.String(20), nullable=False)
    breed = db.Column(db.String(50), nullable=False)
    adult_female_count = db.Column(db.Integer, nullable=False)

    scenario = db.relationship("SimulatorScenario", back_populates="stock_details")
    simulator_farm = db.relationship("SimulatorFarm", back_populates="stock_details")
    farm = db.relationship("Farm")

    __table_args__ = (
        db.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')",
            name="ck_simulator_stock_detail_species_allowed",
        ),
        db.CheckConstraint(
            "adult_female_count >= 0",
            name="ck_simulator_stock_detail_count_non_negative",
        ),
        db.UniqueConstraint(
            "scenario_id",
            "farm_id",
            "species",
            "breed",
            name="uq_simulator_stock_detail_scenario_farm_species_breed",
        ),
        db.UniqueConstraint(
            "scenario_id",
            "simulator_farm_id",
            "species",
            "breed",
            name="uq_simulator_stock_detail_scenario_target_species_breed",
        ),
    )


class SimulatorExpense(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "simulator_expenses"

    scenario_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_scenarios.id"),
        nullable=False,
        index=True,
    )
    simulator_farm_id = db.Column(
        db.String(36),
        db.ForeignKey("simulator_farms.id"),
        nullable=True,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=True, index=True)
    category_code = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    expense_type = db.Column(db.String(20), nullable=False, default="standard")
    amount = db.Column(db.Numeric(12, 2), nullable=True)
    recurrence = db.Column(db.String(20), nullable=True)
    start_month = db.Column(db.Integer, nullable=True)
    loan_principal = db.Column(db.Numeric(12, 2), nullable=True)
    annual_interest_rate = db.Column(db.Numeric(7, 4), nullable=True)
    remaining_term_years = db.Column(db.Numeric(6, 2), nullable=True)
    payment_interval_months = db.Column(db.Integer, nullable=True)
    first_payment_month = db.Column(db.Integer, nullable=True)

    scenario = db.relationship("SimulatorScenario", back_populates="expenses")
    simulator_farm = db.relationship("SimulatorFarm", back_populates="expenses")
    farm = db.relationship("Farm")

    __table_args__ = (
        db.CheckConstraint(
            "expense_type IN ('standard', 'loan')",
            name="ck_simulator_expense_type_allowed",
        ),
        db.CheckConstraint(
            "recurrence IS NULL OR recurrence IN ('monthly', 'yearly', 'once_off')",
            name="ck_simulator_expense_recurrence_allowed",
        ),
        db.CheckConstraint(
            "start_month IS NULL OR start_month BETWEEN 1 AND 12",
            name="ck_simulator_expense_start_month_range",
        ),
        db.CheckConstraint(
            "first_payment_month IS NULL OR first_payment_month BETWEEN 1 AND 12",
            name="ck_simulator_expense_first_payment_month_range",
        ),
        db.CheckConstraint(
            "amount IS NULL OR amount > 0",
            name="ck_simulator_expense_amount_positive",
        ),
        db.CheckConstraint(
            "loan_principal IS NULL OR loan_principal > 0",
            name="ck_simulator_expense_loan_principal_positive",
        ),
        db.CheckConstraint(
            "annual_interest_rate IS NULL OR annual_interest_rate >= 0",
            name="ck_simulator_expense_interest_non_negative",
        ),
        db.CheckConstraint(
            "remaining_term_years IS NULL OR remaining_term_years > 0",
            name="ck_simulator_expense_term_positive",
        ),
        db.CheckConstraint(
            "payment_interval_months IS NULL OR payment_interval_months > 0",
            name="ck_simulator_expense_interval_positive",
        ),
    )
