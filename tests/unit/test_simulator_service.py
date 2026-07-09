from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    Mob,
    SimulatorExpense,
    SimulatorFarm,
    SimulatorRevenueAssumption,
    SimulatorScenario,
    SimulatorStockDetail,
)
from app.services.simulator_service import SimulatorService


def _farm(name: str) -> Farm:
    farm = Farm(name=name, timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    return farm


def _mob_with_balance(
    farm: Farm,
    *,
    species: str,
    breed: str,
    sex: str,
    age_class: str,
    head_count: int,
) -> None:
    mob = Mob(farm_id=farm.id, name=f"{farm.name} {breed} {sex}", status="active")
    group = AnimalGroupType.query.filter_by(
        species=species,
        breed=breed,
        sex=sex,
        age_class=age_class,
    ).first()
    if group is None:
        group = AnimalGroupType(species=species, breed=breed, sex=sex, age_class=age_class)
        db.session.add(group)
    db.session.add(mob)
    db.session.flush()
    db.session.add(
        AnimalGroupBalance(
            mob_id=mob.id,
            animal_group_type_id=group.id,
            head_count=head_count,
        )
    )


def test_simulator_projection_uses_scenario_stock_details_and_expense_scope(app):
    with app.app_context():
        north = _farm("North Farm")
        south = _farm("South Farm")
        scenario = SimulatorScenario(
            name="Base Case",
            projection_year=2026,
            income_inflation_rate=Decimal("5.0000"),
            expense_inflation_rate=Decimal("10.0000"),
        )
        db.session.add(scenario)
        db.session.flush()
        db.session.add_all(
            [
                SimulatorRevenueAssumption(
                    scenario_id=scenario.id,
                    species="Cattle",
                    breed="Bonsmara",
                    yearly_revenue_per_adult_female=Decimal("8000.00"),
                ),
                SimulatorStockDetail(
                    scenario_id=scenario.id,
                    farm_id=north.id,
                    species="Cattle",
                    breed="Bonsmara",
                    adult_female_count=12,
                ),
                SimulatorStockDetail(
                    scenario_id=scenario.id,
                    farm_id=south.id,
                    species="Cattle",
                    breed="Bonsmara",
                    adult_female_count=3,
                ),
                SimulatorExpense(
                    scenario_id=scenario.id,
                    farm_id=north.id,
                    category_code="wages",
                    label="Worker wages",
                    expense_type="standard",
                    amount=Decimal("1000.00"),
                    recurrence="monthly",
                ),
                SimulatorExpense(
                    scenario_id=scenario.id,
                    farm_id=None,
                    category_code="gas",
                    label="Gas refill",
                    expense_type="standard",
                    amount=Decimal("600.00"),
                    recurrence="once_off",
                ),
                SimulatorExpense(
                    scenario_id=scenario.id,
                    farm_id=None,
                    category_code="loan",
                    label="Long equipment loan",
                    expense_type="loan",
                    loan_principal=Decimal("12000.00"),
                    annual_interest_rate=Decimal("0.0000"),
                    remaining_term_years=Decimal("12.00"),
                    payment_interval_months=12,
                    first_payment_month=1,
                ),
            ]
        )
        db.session.commit()

        projection = SimulatorService.build_projection(scenario)

        north_result = next(row for row in projection["farms"] if row["name"] == "North Farm")
        south_result = next(row for row in projection["farms"] if row["name"] == "South Farm")

        assert north_result["stock_rows"][0]["adult_female_count"] == 12
        assert north_result["annual_income"] == Decimal("96000.00")
        assert north_result["annual_expenses"] == Decimal("12000.00")
        assert north_result["annual_net"] == Decimal("84000.00")

        assert south_result["stock_rows"][0]["adult_female_count"] == 3
        assert south_result["annual_income"] == Decimal("24000.00")
        assert south_result["annual_expenses"] == Decimal("0.00")

        assert projection["overall"]["annual_income"] == Decimal("120000.00")
        assert projection["overall"]["annual_expenses"] == Decimal("13600.00")
        assert projection["overall"]["annual_net"] == Decimal("106400.00")

        forecast = projection["overall_forecast"]
        assert forecast["horizon_years"] == 12
        assert forecast["rows"][0]["income"] == Decimal("120000.00")
        assert forecast["rows"][0]["expenses"] == Decimal("13600.00")
        assert forecast["rows"][0]["net"] == Decimal("106400.00")
        assert forecast["rows"][1]["income"] == Decimal("126000.00")
        assert forecast["rows"][1]["expenses"] == Decimal("14200.00")
        assert forecast["rows"][1]["net"] == Decimal("111800.00")
        assert forecast["rows"][11]["expenses"] == Decimal("35237.40")


def test_simulator_expense_recurrence_and_zero_interest_loan_schedule(app):
    with app.app_context():
        scenario = SimulatorScenario(name="Loan Case", projection_year=2026)
        db.session.add(scenario)
        db.session.flush()
        monthly = SimulatorExpense(
            scenario_id=scenario.id,
            category_code="mielies_feeds",
            label="Monthly feed",
            expense_type="standard",
            amount=Decimal("300.00"),
            recurrence="monthly",
        )
        yearly = SimulatorExpense(
            scenario_id=scenario.id,
            category_code="fencing",
            label="Yearly fencing",
            expense_type="standard",
            amount=Decimal("500.00"),
            recurrence="yearly",
        )
        loan = SimulatorExpense(
            scenario_id=scenario.id,
            category_code="loan",
            label="Tractor loan",
            expense_type="loan",
            loan_principal=Decimal("12000.00"),
            annual_interest_rate=Decimal("0.0000"),
            remaining_term_years=Decimal("1.00"),
            payment_interval_months=1,
            first_payment_month=1,
        )
        db.session.add_all([monthly, yearly, loan])
        db.session.commit()

        assert SimulatorService.expense_annual_amount(monthly) == Decimal("3600.00")
        assert SimulatorService.expense_annual_amount(yearly) == Decimal("500.00")
        assert SimulatorService.loan_payment_amount(loan) == Decimal("1000.00")
        assert SimulatorService.expense_annual_amount(loan) == Decimal("12000.00")


def test_simulator_equity_forecast_sums_farm_values_profit_and_liabilities(app):
    with app.app_context():
        north = _farm("Equity North")
        south = _farm("Equity South")
        scenario = SimulatorScenario(name="Equity Case", projection_year=2026)
        db.session.add(scenario)
        db.session.flush()
        north_target = SimulatorFarm(
            scenario_id=scenario.id,
            farm_id=north.id,
            name=north.name,
            initial_farm_value=Decimal("1000.00"),
            farm_value_inflation_rate=Decimal("10.0000"),
        )
        south_target = SimulatorFarm(
            scenario_id=scenario.id,
            farm_id=south.id,
            name=south.name,
            initial_farm_value=Decimal("500.00"),
            farm_value_inflation_rate=Decimal("0.0000"),
        )
        db.session.add_all([north_target, south_target])
        db.session.flush()
        db.session.add_all(
            [
                SimulatorRevenueAssumption(
                    scenario_id=scenario.id,
                    species="Cattle",
                    breed="Bonsmara",
                    yearly_revenue_per_adult_female=Decimal("100.00"),
                ),
                SimulatorStockDetail(
                    scenario_id=scenario.id,
                    simulator_farm_id=north_target.id,
                    farm_id=north.id,
                    species="Cattle",
                    breed="Bonsmara",
                    adult_female_count=10,
                ),
                SimulatorExpense(
                    scenario_id=scenario.id,
                    category_code="loan",
                    label="Small loan",
                    expense_type="loan",
                    loan_principal=Decimal("120.00"),
                    annual_interest_rate=Decimal("0.0000"),
                    remaining_term_years=Decimal("2.00"),
                    payment_interval_months=12,
                    first_payment_month=1,
                ),
            ]
        )
        db.session.commit()

        projection = SimulatorService.build_projection(scenario)
        rows = projection["overall_equity_forecast"]["rows"]

        assert rows[0]["total_assets"] == Decimal("1500.00")
        assert rows[0]["cumulative_profit_loss"] == Decimal("940.00")
        assert rows[0]["liabilities_outstanding"] == Decimal("60.00")
        assert rows[0]["cumulative_equity"] == Decimal("2380.00")

        assert rows[1]["total_assets"] == Decimal("1600.00")
        assert rows[1]["cumulative_profit_loss"] == Decimal("1880.00")
        assert rows[1]["liabilities_outstanding"] == Decimal("0.00")
        assert rows[1]["cumulative_equity"] == Decimal("3480.00")

        assert rows[2]["total_assets"] == Decimal("1710.00")
        assert rows[2]["cumulative_profit_loss"] == Decimal("2880.00")
        assert rows[2]["cumulative_equity"] == Decimal("4590.00")
        assert projection["overall_equity_forecast"]["chart_payload"]["datasets"][0]["key"] == "cumulative_profit_loss"
        assert projection["overall_equity_forecast"]["chart_payload"]["datasets"][1]["key"] == "cumulative_equity"


def test_simulator_interest_bearing_loan_remaining_principal(app):
    with app.app_context():
        loan = SimulatorExpense(
            category_code="loan",
            label="Interest loan",
            expense_type="loan",
            loan_principal=Decimal("1200.00"),
            annual_interest_rate=Decimal("12.0000"),
            remaining_term_years=Decimal("2.00"),
            payment_interval_months=12,
            first_payment_month=1,
        )

        assert SimulatorService.loan_payment_amount(loan) == Decimal("710.04")
        assert SimulatorService.loan_remaining_principal_after_year(loan, 0) == Decimal("633.96")
        assert SimulatorService.loan_remaining_principal_after_year(loan, 1) == Decimal("0.00")


def test_simulator_stock_crud_helpers_seed_and_clear_live_stock(app):
    with app.app_context():
        farm = _farm("Validation Farm")
        _mob_with_balance(
            farm,
            species="Goat",
            breed="Angora",
            sex="ewe",
            age_class="adult",
            head_count=5,
        )
        _mob_with_balance(
            farm,
            species="Goat",
            breed="Angora",
            sex="ram",
            age_class="adult",
            head_count=2,
        )
        scenario = SimulatorScenario(name="Validation", projection_year=2026)
        db.session.add(scenario)
        db.session.commit()

        SimulatorService.seed_stock_details_from_live(scenario)
        db.session.flush()

        seeded = SimulatorStockDetail.query.one()
        assert seeded.adult_female_count == 5

        SimulatorService.upsert_stock_detail(
            scenario,
            MultiDict(
                [
                    ("farm_id", farm.id),
                    ("species", "Goat"),
                    ("breed", "Angora"),
                    ("adult_female_count", "8"),
                ]
            ),
        )
        assert SimulatorStockDetail.query.one().adult_female_count == 8

        SimulatorService.clear_stock_details(scenario)
        db.session.flush()

        assert SimulatorStockDetail.query.count() == 0
