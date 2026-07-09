from decimal import Decimal

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    Mob,
    SimulatorExpense,
    SimulatorFarm,
    SimulatorScenario,
    SimulatorStockDetail,
)
from app.services.simulator_service import SimulatorService


def _farm_with_stock(name: str = "Simulator Farm") -> Farm:
    farm = Farm(name=name, timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
    group = AnimalGroupType.query.filter_by(
        species="Goat",
        breed="Angora",
        sex="ewe",
        age_class="adult",
    ).first()
    if group is None:
        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
    db.session.add(mob)
    db.session.flush()
    db.session.add(
        AnimalGroupBalance(
            mob_id=mob.id,
            animal_group_type_id=group.id,
            head_count=10,
        )
    )
    db.session.commit()
    return farm


def test_simulator_create_can_scope_existing_and_future_farms(client, app):
    with app.app_context():
        selected = _farm_with_stock("Selected Simulator Farm")
        excluded = _farm_with_stock("Excluded Simulator Farm")
        selected_id = str(selected.id)
        excluded_id = str(excluded.id)

    create_response = client.post(
        "/simulator/scenarios",
        data={
            "farm_scope_submitted": "1",
            "farm_ids": [selected_id],
            "new_farm_name": "Planned Lease Farm",
            "name": "Scoped Farm Cash Flow",
            "projection_year": "2026",
            "income_inflation_rate": "0",
            "expense_inflation_rate": "0",
            "notes": "",
        },
        follow_redirects=True,
    )

    assert create_response.status_code == 200
    body = create_response.data.decode("utf-8")
    assert "Planned Lease Farm" in body
    assert "Future farm" in body
    assert "Initial Farm Value" in body
    assert "Farm Value Inflation %" in body
    assert "Overall Equity Forecast" in body
    assert "simulatorEquityForecastData" in body
    assert 'data-equity-series-toggle value="cumulative_profit_loss"' in body
    assert 'data-equity-series-toggle value="cumulative_equity"' in body

    with app.app_context():
        scenario = SimulatorScenario.query.filter_by(name="Scoped Farm Cash Flow").first()
        assert scenario is not None
        scenario_id = str(scenario.id)
        targets = SimulatorFarm.query.filter_by(scenario_id=scenario_id).order_by(SimulatorFarm.name.asc()).all()
        assert [target.name for target in targets] == ["Planned Lease Farm", "Selected Simulator Farm"]
        assert {str(target.farm_id) for target in targets if target.farm_id} == {selected_id}
        assert excluded_id not in {str(target.farm_id) for target in targets if target.farm_id}

        stock_rows = SimulatorStockDetail.query.filter_by(scenario_id=scenario_id).all()
        assert len(stock_rows) == 1
        assert str(stock_rows[0].farm_id) == selected_id

        projection = SimulatorService.build_projection(scenario)
        farm_names = {row["name"] for row in projection["farms"]}
        assert farm_names == {"Planned Lease Farm", "Selected Simulator Farm"}
        future_result = next(row for row in projection["farms"] if row["name"] == "Planned Lease Farm")
        assert future_result["stock_rows"] == []
        target_values = {target.name: target for target in targets}
        assert target_values["Selected Simulator Farm"].initial_farm_value == Decimal("0.00")
        assert target_values["Selected Simulator Farm"].farm_value_inflation_rate == Decimal("0.0000")
        selected_target_id = str(target_values["Selected Simulator Farm"].id)
        future_target_id = str(target_values["Planned Lease Farm"].id)

    update_response = client.post(
        f"/simulator/scenarios/{scenario_id}/edit",
        data={
            "farm_values_submitted": "1",
            "name": "Scoped Farm Cash Flow",
            "projection_year": "2026",
            "income_inflation_rate": "0",
            "expense_inflation_rate": "0",
            "notes": "",
            f"initial_farm_value__{selected_target_id}": "1000000",
            f"farm_value_inflation_rate__{selected_target_id}": "5",
            f"initial_farm_value__{future_target_id}": "250000",
            f"farm_value_inflation_rate__{future_target_id}": "2.5",
        },
        follow_redirects=True,
    )

    assert update_response.status_code == 200
    update_body = update_response.data.decode("utf-8")
    assert "Scenario updated" in update_body
    assert "R1 250 000,00" in update_body
    assert "Overall Equity Forecast" in update_body

    with app.app_context():
        selected_target = db.session.get(SimulatorFarm, selected_target_id)
        future_target = db.session.get(SimulatorFarm, future_target_id)
        assert selected_target.initial_farm_value == Decimal("1000000.00")
        assert selected_target.farm_value_inflation_rate == Decimal("5.0000")
        assert future_target.initial_farm_value == Decimal("250000.00")
        assert future_target.farm_value_inflation_rate == Decimal("2.5000")


def test_simulator_expense_update_and_delete(client, app):
    with app.app_context():
        farm = _farm_with_stock("Expense Edit Farm")
        farm_id = str(farm.id)

    create_response = client.post(
        "/simulator/scenarios",
        data={
            "farm_scope_submitted": "1",
            "farm_ids": [farm_id],
            "name": "Expense Edit Scenario",
            "projection_year": "2026",
            "income_inflation_rate": "0",
            "expense_inflation_rate": "0",
            "notes": "",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200

    with app.app_context():
        scenario = SimulatorScenario.query.filter_by(name="Expense Edit Scenario").first()
        scenario_id = str(scenario.id)
        target = SimulatorFarm.query.filter_by(scenario_id=scenario_id, farm_id=farm_id).one()
        target_id = str(target.id)

    create_expense_response = client.post(
        f"/simulator/scenarios/{scenario_id}/expenses",
        data={
            "expense_type": "standard",
            "simulator_farm_id": target_id,
            "category_code": "wages",
            "label": "Temporary wages",
            "amount": "1000",
            "recurrence": "monthly",
        },
        follow_redirects=True,
    )
    assert create_expense_response.status_code == 200
    expense_body = create_expense_response.data.decode("utf-8")
    assert "Edit Standard Expense" in expense_body
    assert 'value="Temporary wages"' in expense_body

    with app.app_context():
        expense = SimulatorExpense.query.filter_by(label="Temporary wages").one()
        expense_id = str(expense.id)

    update_response = client.post(
        f"/simulator/scenarios/{scenario_id}/expenses/{expense_id}/edit",
        data={
            "expense_type": "standard",
            "simulator_farm_id": "",
            "category_code": "diesel_petrol",
            "label": "Updated diesel",
            "amount": "750",
            "recurrence": "yearly",
        },
        follow_redirects=True,
    )
    assert update_response.status_code == 200
    update_body = update_response.data.decode("utf-8")
    assert "Expense updated" in update_body
    assert "Updated diesel" in update_body

    with app.app_context():
        expense = db.session.get(SimulatorExpense, expense_id)
        assert expense.farm_id is None
        assert expense.simulator_farm_id is None
        assert expense.category_code == "diesel_petrol"
        assert expense.amount == Decimal("750.00")
        assert expense.recurrence == "yearly"

    delete_response = client.post(
        f"/simulator/scenarios/{scenario_id}/expenses/{expense_id}/delete",
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert "Expense removed" in delete_response.data.decode("utf-8")

    with app.app_context():
        assert db.session.get(SimulatorExpense, expense_id) is None


def test_simulator_web_flow_create_stock_crud_clear_inputs_and_delete(client, app):
    with app.app_context():
        farm = _farm_with_stock()
        farm_id = str(farm.id)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"Simulator" in dashboard.data

    index = client.get("/simulator/")
    assert index.status_code == 200
    assert b"Farm Simulator" in index.data

    create_response = client.post(
        "/simulator/scenarios",
        data={
            "name": "Angora Expansion",
            "projection_year": "2026",
            "income_inflation_rate": "5",
            "expense_inflation_rate": "8",
            "notes": "V1 scenario",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    body = create_response.data.decode("utf-8")
    assert "Simulator scenario created" in body
    assert "Angora Expansion" in body
    assert "Income Inflation %" in body
    assert "Expense Inflation %" in body
    assert "Overall Net Forecast" in body
    assert "Overall Equity Forecast" in body

    with app.app_context():
        scenario = SimulatorScenario.query.filter_by(name="Angora Expansion").first()
        assert scenario is not None
        scenario_id = str(scenario.id)
        assert scenario.income_inflation_rate == Decimal("5.0000")
        assert scenario.expense_inflation_rate == Decimal("8.0000")
        assert SimulatorStockDetail.query.filter_by(scenario_id=scenario_id).count() == 1

    revenue_response = client.post(
        f"/simulator/scenarios/{scenario_id}/revenue-assumptions",
        data={
            "species": "Goat",
            "breed": "Angora",
            "yearly_revenue_per_adult_female": "3000",
        },
        follow_redirects=True,
    )
    assert revenue_response.status_code == 200
    assert "Revenue assumption saved" in revenue_response.data.decode("utf-8")

    stock_response = client.post(
        f"/simulator/scenarios/{scenario_id}/stock-details",
        data={
            "farm_id": farm_id,
            "species": "Goat",
            "breed": "Angora",
            "adult_female_count": "15",
        },
        follow_redirects=True,
    )
    assert stock_response.status_code == 200
    assert "Stock detail saved" in stock_response.data.decode("utf-8")

    expense_response = client.post(
        f"/simulator/scenarios/{scenario_id}/expenses",
        data={
            "expense_type": "standard",
            "farm_id": farm_id,
            "category_code": "wages",
            "label": "Worker wages",
            "amount": "1000",
            "recurrence": "monthly",
        },
        follow_redirects=True,
    )
    assert expense_response.status_code == 200
    expense_body = expense_response.data.decode("utf-8")
    assert "Expense saved" in expense_body
    assert "R45 000,00" in expense_body
    assert "R1 000,00" in expense_body
    assert "R12 000,00" in expense_body
    assert "R33 000,00" in expense_body

    loan_response = client.post(
        f"/simulator/scenarios/{scenario_id}/expenses",
        data={
            "expense_type": "loan",
            "farm_id": "",
            "category_code": "loan",
            "label": "Small tractor loan",
            "loan_principal": "6000",
            "annual_interest_rate": "0",
            "remaining_term_years": "1",
            "payment_interval_months": "1",
            "first_payment_month": "1",
        },
        follow_redirects=True,
    )
    assert loan_response.status_code == 200
    loan_body = loan_response.data.decode("utf-8")
    assert "Small tractor loan" in loan_body
    assert "R500,00" in loan_body
    assert "R6 000,00" in loan_body
    assert "R27 000,00" in loan_body

    clear_response = client.post(
        f"/simulator/scenarios/{scenario_id}/stock-details/clear",
        follow_redirects=True,
    )
    assert clear_response.status_code == 200
    clear_body = clear_response.data.decode("utf-8")
    assert "Stock details cleared" in clear_body
    assert "No scenario stock rows yet." in clear_body

    seed_response = client.post(
        f"/simulator/scenarios/{scenario_id}/stock-details/seed",
        follow_redirects=True,
    )
    assert seed_response.status_code == 200
    assert "Stock details refreshed from live adult female counts" in seed_response.data.decode("utf-8")

    delete_response = client.post(
        f"/simulator/scenarios/{scenario_id}/delete",
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert "Scenario deleted" in delete_response.data.decode("utf-8")

    with app.app_context():
        assert SimulatorScenario.query.filter_by(id=scenario_id).first() is None


def test_farm_deletion_cleans_simulator_farm_links(client, app):
    with app.app_context():
        farm = Farm(name="Simulator Delete Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        scenario = SimulatorScenario(name="Delete Linked Scenario", projection_year=2026)
        db.session.add(scenario)
        db.session.flush()
        expense = SimulatorExpense(
            scenario_id=scenario.id,
            farm_id=farm.id,
            category_code="wages",
            label="Farm worker",
            expense_type="standard",
            amount=Decimal("1000.00"),
            recurrence="monthly",
        )
        stock_detail = SimulatorStockDetail(
            scenario_id=scenario.id,
            farm_id=farm.id,
            species="Cattle",
            breed="Bonsmara",
            adult_female_count=1,
        )
        db.session.add_all([expense, stock_detail])
        db.session.commit()
        farm_id = str(farm.id)
        expense_id = str(expense.id)

    response = client.post(f"/farms/{farm_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Farm, farm_id) is None
        assert db.session.get(SimulatorExpense, expense_id).farm_id is None
        assert SimulatorStockDetail.query.filter_by(farm_id=farm_id).count() == 0
