from decimal import Decimal

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    Mob,
    SimulatorExpense,
    SimulatorScenario,
    SimulatorStockDetail,
)


def _farm_with_stock() -> Farm:
    farm = Farm(name="Simulator Farm", timezone="UTC", active=True)
    db.session.add(farm)
    db.session.flush()
    mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
    group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
    db.session.add_all([mob, group])
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
        farm = Farm(name="Simulator Delete Farm", timezone="UTC", active=True)
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
