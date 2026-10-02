import pytest

from app.extensions import db
from app.models import (
    AnimalCohort, AnimalGroupBalance, AnimalGroupType, Farm, Mob,
    GrazingAllocationGroupAssignment, MobEvent, Paddock, StockLedgerEntry, User, UserFarmRole,
)


@pytest.fixture
def aggregate_stock(app):
    with app.app_context():
        farm = Farm(name="Aggregate API Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        source = Mob(farm_id=farm.id, name="Source", status="active")
        destination = Mob(farm_id=farm.id, name="Destination", status="active")
        paddock = Paddock(farm_id=farm.id, name="Count Camp", area_ha=10, grazeable_area_ha=10)
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        user = User(email="aggregate@example.com", name="Manager", active=True)
        user.set_password("test-password")
        db.session.add_all([source, destination, paddock, group, user])
        db.session.flush()
        db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))
        cohort_ids = []
        for index, (head, state) in enumerate([(6, "pregnant"), (4, "not_pregnant")]):
            cohort = AnimalCohort(
                animal_group_type_id=group.id, origin_farm_id=farm.id,
                origin="manual", reproductive_state=state,
            )
            db.session.add(cohort)
            db.session.flush()
            db.session.add(AnimalGroupBalance(
                id=f"00000000-0000-0000-0000-{index:012d}",
                mob_id=source.id, animal_group_type_id=group.id,
                cohort_id=cohort.id, head_count=head,
            ))
            cohort_ids.append(str(cohort.id))
        db.session.commit()
        return dict(farm=str(farm.id), source=str(source.id), destination=str(destination.id),
                    group=str(group.id), paddock=str(paddock.id), cohorts=cohort_ids)


def _headers(client):
    response = client.post("/api/mobile/v1/auth/login", json={
        "email": "aggregate@example.com", "password": "test-password", "device_name": "Tests",
    })
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['token']}"}


def _total(mob_id):
    return sum(row.head_count for row in AnimalGroupBalance.query.filter_by(mob_id=mob_id).all())


def test_rest_balances_and_recounts_use_all_cohorts(client, app, aggregate_stock):
    stock = aggregate_stock
    url = f"/api/mobs/{stock['source']}"
    assert client.get(f"{url}/balances").get_json() == [
        {"animal_group_type_id": stock["group"], "head_count": 10},
    ]
    body = dict(animal_group_type_id=stock["group"], event_type="count", quantity=10)
    unchanged = client.post(f"{url}/animal-groups/adjust", json=body)
    assert unchanged.status_code == 200
    assert unchanged.get_json()["ledger_ids"] == []
    with app.app_context():
        assert StockLedgerEntry.query.count() == 0

    body["quantity"] = 7
    response = client.post(f"{url}/animal-groups/adjust", json=body)
    assert response.status_code == 201
    assert len(response.get_json()["ledger_ids"]) == 2
    assert "ledger_id" not in response.get_json()
    with app.app_context():
        assert _total(stock["source"]) == 7
        assert [AnimalGroupBalance.query.filter_by(cohort_id=key).one().head_count
                for key in stock["cohorts"]] == [4, 3]


def test_rest_addition_is_unrecorded_and_zero_count_removes_all(client, app, aggregate_stock):
    stock = aggregate_stock
    url = f"/api/mobs/{stock['source']}/animal-groups/adjust"
    response = client.post(url, json={
        "animal_group_type_id": stock["group"], "event_type": "count", "quantity": 12,
    })
    assert response.status_code == 201
    assert response.get_json()["ledger_ids"] == [response.get_json()["ledger_id"]]
    with app.app_context():
        rows = AnimalGroupBalance.query.filter_by(mob_id=stock["source"]).all()
        added = next(row for row in rows if row.cohort_id not in stock["cohorts"])
        assert added.head_count == 2
        assert added.cohort.reproductive_state == "not_recorded"
        assert added.cohort.lactation_state == "not_recorded"
        assert _total(stock["source"]) == 12
    assert client.post(url, json={
        "animal_group_type_id": stock["group"], "event_type": "count", "quantity": 0,
    }).status_code == 201
    assert client.get(f"/api/mobs/{stock['source']}/balances").get_json() == []


def test_web_recount_records_one_event_for_combined_total(client, app, aggregate_stock):
    stock = aggregate_stock
    response = client.post(f"/mobs/{stock['source']}/adjust", data={
        "species": "Sheep", "breed": "Merino", "sex": "ewe", "age_class": "adult",
        "event_type": "count", "quantity": "7", "note": "Combined field count",
    })
    assert response.status_code == 302
    with app.app_context():
        assert _total(stock["source"]) == 7
        event = MobEvent.query.filter_by(mob_id=stock["source"]).one()
        assert "10 -> 7 head" in event.description
        assert "Combined field count" in event.description
        assert sum(row.quantity for row in StockLedgerEntry.query.all()) == 3


def test_mobile_aggregate_count_transfer_and_replay(client, app, aggregate_stock):
    stock = aggregate_stock
    headers = _headers(client)
    assert client.get("/api/mobile/v1/bootstrap", headers=headers).get_json()["sync"][
        "aggregate_stock_operations"
    ] is True
    commands = [
        dict(client_command_id="aggregate-count", type="stock_count.record", farm_id=stock["farm"],
             payload=dict(mob_id=stock["source"], animal_group_type_id=stock["group"], quantity=7)),
        dict(client_command_id="aggregate-transfer", type="mob.transfer", farm_id=stock["farm"],
             payload=dict(source_mob_id=stock["source"], destination_mob_id=stock["destination"],
                          transfers=[dict(animal_group_type_id=stock["group"], quantity=4)])),
    ]
    response = client.post("/api/mobile/v1/sync/commands", headers=headers, json={"commands": commands})
    assert response.status_code == 200
    assert [result["status"] for result in response.get_json()["results"]] == ["applied", "applied"]
    with app.app_context():
        assert (_total(stock["source"]), _total(stock["destination"])) == (3, 4)
        ledger_count = StockLedgerEntry.query.count()
    replay = client.post("/api/mobile/v1/sync/commands", headers=headers, json={"commands": commands})
    assert replay.status_code == 200
    with app.app_context():
        assert StockLedgerEntry.query.count() == ledger_count
        assert (_total(stock["source"]), _total(stock["destination"])) == (3, 4)


def test_mobile_new_offline_type_can_transfer_move_and_recount_to_zero(client, app, aggregate_stock):
    stock = aggregate_stock
    headers = _headers(client)
    taxonomy = dict(species="Sheep", breed="Dohne", sex="wether", age_class="young")
    commands = [
        dict(client_command_id="new-type-count", type="stock_count.record", farm_id=stock["farm"],
             payload=dict(mob_id=stock["source"], animal_group_type=taxonomy, quantity=5)),
        dict(client_command_id="new-type-transfer", type="mob.transfer", farm_id=stock["farm"],
             payload=dict(source_mob_id=stock["source"], destination_mob_id=stock["destination"],
                          transfers=[dict(animal_group_type=taxonomy, quantity=2)])),
        dict(client_command_id="new-type-move", type="mob.move", farm_id=stock["farm"],
             payload=dict(mob_id=stock["source"], allocation_mode="counts", allocations=[
                 dict(paddock_id=stock["paddock"], group_counts=[
                     dict(animal_group_type_id=stock["group"], head_count=10),
                     dict(animal_group_type=taxonomy, head_count=3),
                 ]),
             ])),
        dict(client_command_id="new-type-zero", type="stock_count.record", farm_id=stock["farm"],
             payload=dict(mob_id=stock["source"], animal_group_type=taxonomy, quantity=0)),
    ]
    response = client.post("/api/mobile/v1/sync/commands", headers=headers, json={"commands": commands})
    assert response.status_code == 200
    results = response.get_json()["results"]
    assert [result["status"] for result in results] == ["applied"] * 4, results
    with app.app_context():
        group = AnimalGroupType.query.filter_by(**taxonomy).one()
        assert AnimalGroupBalance.query.filter_by(mob_id=stock["source"], animal_group_type_id=group.id).count() == 0
        moved = AnimalGroupBalance.query.filter_by(mob_id=stock["destination"], animal_group_type_id=group.id).one()
        assert moved.head_count == 2
        assert moved.cohort.reproductive_state == "not_recorded"
        assignments = GrazingAllocationGroupAssignment.query.all()
        assert [(str(row.animal_group_type_id), row.head_count) for row in assignments] == [(stock["group"], 10)]


@pytest.mark.parametrize("quantity", [-1, None, "invalid", 1.5, True])
def test_invalid_recount_leaves_all_cohorts_untouched(client, app, aggregate_stock, quantity):
    stock = aggregate_stock
    response = client.post(f"/api/mobs/{stock['source']}/animal-groups/adjust", json={
        "animal_group_type_id": stock["group"], "event_type": "count", "quantity": quantity,
    })
    assert response.status_code == 400
    with app.app_context():
        assert _total(stock["source"]) == 10
        assert StockLedgerEntry.query.count() == 0
