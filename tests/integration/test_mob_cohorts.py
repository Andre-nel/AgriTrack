import re

import pytest

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalCohortLineage,
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    FemaleStatusObservation,
    Mob,
    StockLedgerEntry,
)
from app.services.cohort_service import CohortService


@pytest.fixture
def cohort_data(app):
    with app.app_context():
        farm = Farm(name="Cohort Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        mob = Mob(farm_id=farm.id, name="Combined Mob", status="active")
        target = Mob(farm_id=farm.id, name="Destination Mob", status="active")
        archived = Mob(farm_id=farm.id, name="Archived Mob", status="archived")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        ram_group = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="adult")
        db.session.add_all([mob, target, archived, group, ram_group])
        db.session.flush()
        pregnant = CohortService.create_cohort(
            animal_group_type_id=group.id, origin_farm_id=farm.id,
            reproductive_state="pregnant", expected_litter_size="twins",
            lactation_state="dry", offspring_at_foot="none",
        )
        other = CohortService.create_cohort(
            animal_group_type_id=group.id, origin_farm_id=farm.id,
            reproductive_state="not_pregnant",
        )
        balances = [
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, cohort_id=pregnant.id, head_count=6),
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, cohort_id=other.id, head_count=4),
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=2),
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=ram_group.id, head_count=3),
        ]
        db.session.add_all(balances)
        db.session.commit()
        return {
            "mob": str(mob.id), "target": str(target.id), "archived": str(archived.id),
            "group": str(group.id), "pregnant": str(pregnant.id), "other": str(other.id),
            "balance": str(balances[0].id), "legacy": str(balances[2].id),
        }


def test_balances_are_combined_and_cohort_management_is_separate(client, app, cohort_data):
    data = cohort_data
    body = client.get(f"/mobs/{data['mob']}").get_data(as_text=True)
    assert f'href="/mobs/{data["mob"]}/cohorts"' in body
    assert "Manage Cohorts" in body
    assert body.count('class="balance-row"') == 2
    assert re.search(r'data-group-id="' + data["group"] + r'"\s+.*?data-head-count="12"', body, re.S)
    assert 'name="cohort_id"' not in body
    assert 'name="source_cohort_id"' not in body
    assert "data-cohort-id" not in body
    assert "Reproductive State" not in body
    assert "Record Female Cohort State" not in body
    assert "Sheep | Merino | ewe | adult (available: 12)" in body
    assert "cohort:" not in body

    response = client.get(f"/mobs/{data['mob']}/cohorts")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f'href="/mobs/{data["mob"]}"' in body
    assert "Back to Mob" in body
    assert "Expected Litter Size" in body
    assert "pregnant" in body and "twins" in body and "dry" in body
    assert f"#{data['pregnant'][:8]}" in body
    assert f"#{data['legacy'][:8]}" in body
    assert "Record Female Cohort State" in body
    with app.app_context():
        assert AnimalCohort.query.count() == 2
        assert db.session.get(AnimalGroupBalance, data["legacy"]).cohort_id is None


def test_cohort_page_handles_empty_archived_and_missing_mobs(client, cohort_data):
    empty = client.get(f"/mobs/{cohort_data['target']}/cohorts")
    assert empty.status_code == 200
    assert b"No positive cohort balances" in empty.data
    assert b"Record Female Cohort State" not in empty.data
    for mob_id in [cohort_data["archived"], "missing-mob"]:
        assert client.get(f"/mobs/{mob_id}/cohorts").status_code == 404
        assert client.post(f"/mobs/{mob_id}/cohorts/adjust").status_code == 404
        assert client.post(f"/mobs/{mob_id}/cohorts/transfer").status_code == 404


@pytest.mark.parametrize("route", ["adjust", "transfer"])
@pytest.mark.parametrize("selection", ["missing", "foreign", "empty"])
def test_cohort_stock_rejects_stale_foreign_and_empty_balances(client, app, cohort_data, route, selection):
    data = cohort_data
    with app.app_context():
        selected_id = "missing-balance"
        if selection == "foreign":
            balance = AnimalGroupBalance(
                mob_id=data["target"], animal_group_type_id=data["group"], head_count=8,
            )
            db.session.add(balance)
            db.session.commit()
            selected_id = str(balance.id)
        elif selection == "empty":
            balance = db.session.get(AnimalGroupBalance, data["legacy"])
            balance.head_count = 0
            db.session.commit()
            selected_id = str(balance.id)
        cohort_count = AnimalCohort.query.count()

    response = client.post(f"/mobs/{data['mob']}/cohorts/{route}", data={
        "balance_id": selected_id, "quantity": "2", "event_type": "purchase",
        "destination_mob_id": data["target"],
    }, follow_redirects=True)
    assert b"Selected cohort is no longer available in this mob" in response.data
    with app.app_context():
        assert StockLedgerEntry.query.count() == 0
        assert AnimalCohort.query.count() == cohort_count
        assert db.session.get(AnimalGroupBalance, data["balance"]).head_count == 6


def test_targeted_adjustment_uses_selected_type_and_retains_state(client, app, cohort_data):
    data = cohort_data
    response = client.post(f"/mobs/{data['mob']}/cohorts/adjust", data={
        "balance_id": data["balance"], "event_type": "purchase", "quantity": "2",
        "species": "Cattle", "sex": "bul", "cohort_id": data["other"],
    })
    assert response.location.endswith(f"/mobs/{data['mob']}/cohorts")
    with app.app_context():
        selected = db.session.get(AnimalGroupBalance, data["balance"])
        assert selected.head_count == 8
        assert selected.cohort.reproductive_state == "pregnant"
        assert AnimalCohort.query.count() == 2
        entry = StockLedgerEntry.query.one()
        assert str(entry.cohort_id) == data["pregnant"]
        assert str(entry.animal_group_type_id) == data["group"]
        assert AnimalGroupBalance.query.filter_by(cohort_id=data["other"]).one().head_count == 4


def test_targeted_zero_count_removes_only_selected_cohort(client, app, cohort_data):
    data = cohort_data
    response = client.post(f"/mobs/{data['mob']}/cohorts/adjust", data={
        "balance_id": data["balance"], "event_type": "count", "quantity": "0",
    }, follow_redirects=True)
    assert b"Count set to 0" in response.data
    with app.app_context():
        selected = db.session.get(AnimalGroupBalance, data["balance"])
        assert selected is None or selected.head_count == 0
        assert sum(row.head_count for row in AnimalGroupBalance.query.filter_by(
            mob_id=data["mob"], animal_group_type_id=data["group"]
        )) == 6
        assert StockLedgerEntry.query.one().quantity == 6


def test_legacy_matching_count_does_not_create_cohort(client, app, cohort_data):
    data = cohort_data
    response = client.post(f"/mobs/{data['mob']}/cohorts/adjust", data={
        "balance_id": data["legacy"], "event_type": "count", "quantity": "2",
    }, follow_redirects=True)
    assert b"Count matches current balance" in response.data
    with app.app_context():
        assert db.session.get(AnimalGroupBalance, data["legacy"]).cohort_id is None
        assert AnimalCohort.query.count() == 2
        assert StockLedgerEntry.query.count() == 0


@pytest.mark.parametrize("quantity", [None, "", "1.5", "-1"])
def test_invalid_targeted_count_cannot_clear_cohort(client, app, cohort_data, quantity):
    data = cohort_data
    payload = {"balance_id": data["balance"], "event_type": "count"}
    if quantity is not None:
        payload["quantity"] = quantity
    client.post(f"/mobs/{data['mob']}/cohorts/adjust", data=payload)
    with app.app_context():
        assert db.session.get(AnimalGroupBalance, data["balance"]).head_count == 6
        assert StockLedgerEntry.query.count() == 0


@pytest.mark.parametrize("route", ["adjust", "transfer"])
def test_invalid_legacy_mutation_rolls_back_created_cohort(client, app, cohort_data, route):
    data = cohort_data
    client.post(f"/mobs/{data['mob']}/cohorts/{route}", data={
        "balance_id": data["legacy"], "event_type": "sale", "quantity": "99",
        "destination_mob_id": data["target"],
    })
    with app.app_context():
        assert db.session.get(AnimalGroupBalance, data["legacy"]).cohort_id is None
        assert AnimalCohort.query.count() == 2
        assert StockLedgerEntry.query.count() == 0


def test_targeted_partial_transfer_retains_state_and_lineage(client, app, cohort_data):
    data = cohort_data
    response = client.post(f"/mobs/{data['mob']}/cohorts/transfer", data={
        "balance_id": data["balance"], "quantity": "2", "destination_mob_id": data["target"],
    })
    assert response.location.endswith(f"/mobs/{data['mob']}/cohorts")
    with app.app_context():
        assert db.session.get(AnimalGroupBalance, data["balance"]).head_count == 4
        target_balance = AnimalGroupBalance.query.filter_by(mob_id=data["target"]).one()
        assert target_balance.head_count == 2
        assert target_balance.cohort.reproductive_state == "pregnant"
        assert target_balance.cohort.expected_litter_size == "twins"
        link = AnimalCohortLineage.query.one()
        assert str(link.parent_cohort_id) == data["pregnant"]
        assert link.child_cohort_id == target_balance.cohort_id
        assert FemaleStatusObservation.query.count() == 0


def test_partial_female_state_observation_returns_to_cohorts(client, app, cohort_data):
    data = cohort_data
    response = client.post(f"/mobs/{data['mob']}/female-status", data={
        "balance_id": data["balance"], "quantity": "3", "observed_on": "2026-09-22",
        "reproductive_state": "parturated", "expected_litter_size": "not_recorded",
        "lactation_state": "lactating", "offspring_at_foot": "twins",
    })
    assert response.location.endswith(f"/mobs/{data['mob']}/cohorts")
    with app.app_context():
        assert db.session.get(AnimalGroupBalance, data["balance"]).head_count == 3
        observation = FemaleStatusObservation.query.one()
        assert observation.quantity == 3
        assert str(observation.source_cohort_id) == data["pregnant"]
        assert str(observation.result_cohort_id) != data["pregnant"]
        assert sum(row.head_count for row in AnimalGroupBalance.query.filter_by(
            mob_id=data["mob"], animal_group_type_id=data["group"]
        )) == 12
        assert StockLedgerEntry.query.count() == 0
