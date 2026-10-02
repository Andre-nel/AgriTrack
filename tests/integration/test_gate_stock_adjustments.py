"""Selected-camp stock changes use the existing open-gate allocation pool."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    MobEvent,
    Paddock,
    PaddockGate,
    StockLedgerEntry,
    User,
    UserFarmRole,
)
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


STARTED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)
ADJUSTED_AT = datetime(2026, 6, 2, tzinfo=timezone.utc)
IN_EVENTS = ["birth", "purchase", "transfer_in", "adjustment_in"]
OUT_EVENTS = ["death", "sale", "missing", "transfer_out", "adjustment_out"]


def _gate(left, right, *, status="open", active=True):
    # The gates were already open when the stock was recorded. Creating the model
    # directly avoids the separate gate-opening operation moving existing stock.
    first_id, second_id = sorted([str(left.id), str(right.id)])
    gate = PaddockGate(
        farm_id=left.farm_id,
        paddock_a_id=first_id,
        paddock_b_id=second_id,
        status=status,
        active=active,
        source="manual",
    )
    db.session.add(gate)
    db.session.flush()
    return gate


def _place(mob, paddocks, kid, adult, kid_counts, adult_counts):
    allocations = []
    for paddock, kids, adults in zip(paddocks, kid_counts, adult_counts):
        group_counts = [
            {"animal_group_type_id": str(group.id), "head_count": count}
            for group, count in [(kid, kids), (adult, adults)]
            if count
        ]
        if group_counts:
            allocations.append({"paddock_id": str(paddock.id), "group_counts": group_counts})
    MovementService.move_mob(
        mob=mob,
        allocations=allocations,
        destination_farm_id=str(mob.farm_id),
        allocation_mode="counts",
        apply_open_gate_network=False,
        allow_cross_farm_allocations=True,
        when=STARTED_AT,
    )


def _stock_in_camps(*, kids=(1, 9, 20), adults=(5, 7, 11), cross_farm=False):
    home = Farm(name="Home", timezone="SAST", active=True)
    db.session.add(home)
    db.session.flush()
    if cross_farm:
        selected_farm = Farm(name="Selected camp farm", timezone="SAST", active=True)
        third_farm = Farm(name="Third camp farm", timezone="SAST", active=True)
        db.session.add_all([selected_farm, third_farm])
        db.session.flush()
    else:
        selected_farm = third_farm = home

    paddocks = [
        Paddock(
            id=f"00000000-0000-0000-0000-{index:012d}",
            farm_id=farm.id,
            name=name,
            area_ha=area,
            grazeable_area_ha=area,
        )
        for index, (name, area, farm) in enumerate(
            [("Selected", 1, selected_farm), ("Connected", 3, selected_farm),
             ("Other camp", 2, third_farm)],
            start=1,
        )
    ]
    mob = Mob(farm_id=home.id, name="Lambing mob", status="active")
    other_mob = Mob(farm_id=home.id, name="Other mob", status="active")
    kid = AnimalGroupType(species="Goat", breed="Angora", sex="mixed", age_class="kid")
    adult = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
    db.session.add_all([*paddocks, mob, other_mob, kid, adult])
    db.session.flush()

    balances = []
    total_kids = sum(kids)
    first_cohort_count = total_kids * 3 // 5
    for count in [first_cohort_count, total_kids - first_cohort_count]:
        if not count:
            continue
        cohort = CohortService.create_cohort(
            animal_group_type_id=kid.id, origin_farm_id=home.id, origin="birth",
        )
        balance = AnimalGroupBalance(
            mob_id=mob.id, animal_group_type_id=kid.id, cohort_id=cohort.id, head_count=count,
        )
        db.session.add(balance)
        balances.append(balance)
    if sum(adults):
        db.session.add(AnimalGroupBalance(
            mob_id=mob.id, animal_group_type_id=adult.id, head_count=sum(adults),
        ))
    db.session.add(AnimalGroupBalance(
        mob_id=other_mob.id, animal_group_type_id=kid.id, head_count=8,
    ))
    db.session.flush()
    _place(mob, paddocks, kid, adult, kids, adults)
    _place(other_mob, paddocks, kid, adult, (2, 6, 0), (0, 0, 0))
    gate = _gate(paddocks[0], paddocks[1])
    db.session.commit()
    return SimpleNamespace(
        home=home, mob=mob, other_mob=other_mob, kid=kid, adult=adult,
        selected=paddocks[0], connected=paddocks[1], other=paddocks[2],
        paddocks=paddocks, balances=balances, gate=gate,
    )


def _counts(mob):
    session = GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()
    return {
        str(allocation.paddock_id): {
            str(assignment.animal_group_type_id): int(assignment.head_count)
            for assignment in allocation.group_assignments
        }
        for allocation in session.allocations
    }


def _assert_counts(stock, kids, adults=(5, 7, 11)):
    counts = _counts(stock.mob)
    expected = {}
    for paddock, kid_count, adult_count in zip(stock.paddocks, kids, adults):
        group_counts = {
            str(group.id): count
            for group, count in [(stock.kid, kid_count), (stock.adult, adult_count)]
            if count
        }
        if group_counts:
            expected[str(paddock.id)] = group_counts
    assert counts == expected
    assert StockService.group_head_count(stock.mob.id, stock.kid.id) == sum(kids)
    assert StockService.group_head_count(stock.mob.id, stock.adult.id) == sum(adults)
    assert _counts(stock.other_mob) == {
        str(stock.selected.id): {str(stock.kid.id): 2},
        str(stock.connected.id): {str(stock.kid.id): 6},
    }


def _adjust(stock, event_type, quantity, *, selected=True, cohort_id=None):
    arguments = dict(
        mob_id=stock.mob.id,
        farm_id=stock.mob.farm_id,
        animal_group_type_id=stock.kid.id,
        event_type=StockEventType(event_type),
        quantity=quantity,
        allocation_paddock_id=str(stock.selected.id) if selected else None,
        event_time=ADJUSTED_AT,
    )
    if cohort_id:
        return [StockService.adjust_stock(cohort_id=cohort_id, **arguments)]
    return StockService.adjust_group_stock(**arguments)


def _database_snapshot():
    models = [
        AnimalCohort, AnimalGroupBalance, StockLedgerEntry, MobEvent,
        GrazingSession, GrazingAllocation, GrazingAllocationGroupAssignment,
        GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory,
    ]
    return {
        model.__tablename__: [
            tuple(getattr(row, column.name) for column in model.__table__.columns)
            for row in model.query.order_by(model.id).all()
        ]
        for model in models
    }


@pytest.mark.parametrize("event_type", IN_EVENTS + ["count"])
def test_aggregate_stock_in_distributes_only_added_animals(app, event_type):
    stock = _stock_in_camps()
    original_cohorts = {str(row.id): row.head_count for row in stock.balances}

    entries = _adjust(stock, event_type, 34 if event_type == "count" else 4)
    db.session.commit()

    _assert_counts(stock, (2, 12, 20))
    assert len(entries) == 1
    assert entries[0].quantity == 4
    assert entries[0].event_type.value == ("adjustment_in" if event_type == "count" else event_type)
    assert entries[0].event_time.replace(tzinfo=timezone.utc) == ADJUSTED_AT
    assert all(db.session.get(AnimalGroupBalance, key).head_count == count
               for key, count in original_cohorts.items())


@pytest.mark.parametrize("event_type", OUT_EVENTS + ["count"])
def test_aggregate_stock_out_uses_connected_counts_and_combined_count_total(app, event_type):
    stock = _stock_in_camps()

    entries = _adjust(stock, event_type, 27 if event_type == "count" else 3)
    db.session.commit()

    # Three removals exceed the selected camp's one kid, but fit the pool of ten.
    _assert_counts(stock, (1, 6, 20))
    assert sorted(row.head_count for row in stock.balances) == [11, 16]
    assert sum(entry.quantity for entry in entries) == 3
    assert {entry.event_type.value for entry in entries} == {
        "missing" if event_type == "count" else event_type,
    }


@pytest.mark.parametrize("event_type", IN_EVENTS + OUT_EVENTS)
def test_specific_cohort_uses_the_same_gate_pool_once(app, event_type):
    stock = _stock_in_camps()
    selected_balance, other_balance = stock.balances
    increasing = event_type in IN_EVENTS

    entries = _adjust(
        stock, event_type, 4 if increasing else 3,
        cohort_id=str(selected_balance.cohort_id),
    )
    db.session.commit()

    _assert_counts(stock, (2, 12, 20) if increasing else (1, 6, 20))
    assert selected_balance.head_count == (22 if increasing else 15)
    assert other_balance.head_count == 12
    assert len(entries) == 1
    assert str(entries[0].cohort_id) == str(selected_balance.cohort_id)


@pytest.mark.parametrize("entry_point", ["aggregate", "cohort"])
@pytest.mark.parametrize("kids, available, requested", [
    ((1, 9, 20), 10, 11),
    ((0, 0, 30), 0, 1),
])
def test_insufficient_connected_stock_rolls_back_despite_disconnected_stock(
    app, entry_point, kids, available, requested,
):
    stock = _stock_in_camps(kids=kids)
    before = _database_snapshot()

    with pytest.raises(ValueError, match=rf"{available} available, {requested} requested"):
        _adjust(
            stock, "sale", requested,
            cohort_id=str(stock.balances[0].cohort_id) if entry_point == "cohort" else None,
        )
    db.session.rollback()

    assert _database_snapshot() == before
    _assert_counts(stock, kids)


@pytest.mark.parametrize("kids, quantity, expected", [
    ((1, 9, 20), 10, (0, 0, 20)),
    ((1, 1, 28), 1, (0, 1, 28)),
])
def test_outgoing_apportionment_handles_exact_availability_and_equal_remainders(app, kids, quantity, expected):
    stock = _stock_in_camps(kids=kids)

    _adjust(stock, "sale", quantity)
    db.session.commit()

    _assert_counts(stock, expected)


def test_outgoing_adjustment_can_start_in_camp_with_no_selected_animal_type(app):
    stock = _stock_in_camps(kids=(0, 10, 20))

    _adjust(stock, "death", 3)
    db.session.commit()

    _assert_counts(stock, (0, 7, 20))


def test_birth_of_new_animal_type_reaches_previously_unoccupied_connected_camp(app):
    stock = _stock_in_camps(kids=(0, 0, 0), adults=(5, 0, 11))
    assert str(stock.connected.id) not in _counts(stock.mob)

    _adjust(stock, "birth", 4)
    db.session.commit()

    _assert_counts(stock, (1, 3, 0), adults=(5, 0, 11))


@pytest.mark.parametrize("event_type, quantity, expected", [
    ("birth", 6, (2, 12, 22)),
    ("sale", 6, (1, 7, 16)),
])
def test_transitive_cross_farm_network_is_anchored_on_selected_camp(app, event_type, quantity, expected):
    stock = _stock_in_camps(cross_farm=True)
    _gate(stock.connected, stock.other)
    db.session.commit()
    assert stock.mob.farm_id != stock.selected.farm_id
    assert stock.other.farm_id != stock.selected.farm_id

    _adjust(stock, event_type, quantity)
    db.session.commit()

    _assert_counts(stock, expected)
    assert str(stock.mob.farm_id) == str(stock.home.id)


@pytest.mark.parametrize("excluded_by", ["closed_gate", "inactive_gate", "inactive_camp", "inactive_farm"])
@pytest.mark.parametrize("event_type, quantity, expected", [
    ("birth", 4, (2, 12, 20)),
    ("sale", 3, (1, 6, 20)),
])
def test_gate_pool_excludes_closed_or_inactive_connections(app, excluded_by, event_type, quantity, expected):
    stock = _stock_in_camps()
    gate = _gate(stock.connected, stock.other)
    if excluded_by == "closed_gate":
        gate.status = "closed"
    elif excluded_by == "inactive_gate":
        gate.active = False
    elif excluded_by == "inactive_camp":
        stock.other.status = "inactive"
    else:
        inactive_farm = Farm(name="Inactive destination farm", timezone="SAST", active=False)
        db.session.add(inactive_farm)
        db.session.flush()
        stock.other.farm_id = inactive_farm.id
    db.session.commit()

    _adjust(stock, event_type, quantity)
    db.session.commit()

    _assert_counts(stock, expected)


@pytest.mark.parametrize("event_type, quantity, expected", [
    ("birth", 4, (5, 9, 20)),
    ("sale", 1, (0, 9, 20)),
])
def test_selected_camp_without_open_connections_retains_local_behavior(app, event_type, quantity, expected):
    stock = _stock_in_camps()
    stock.gate.status = "closed"
    db.session.commit()

    _adjust(stock, event_type, quantity)
    db.session.commit()

    _assert_counts(stock, expected)


@pytest.mark.parametrize("areas, grazeable_areas, quantity, expected", [
    ((10, 10), (1, 3), 5, (2, 13, 20)),
    ((1, 3), (0, 0), 5, (2, 13, 20)),
    ((0, 0), (0, 0), 5, (4, 11, 20)),
    ((1, 1), (1, 1), 1, (2, 9, 20)),
])
def test_incoming_whole_head_rounding_and_area_fallbacks(app, areas, grazeable_areas, quantity, expected):
    stock = _stock_in_camps()
    for paddock, area, grazeable in zip(stock.paddocks[:2], areas, grazeable_areas):
        paddock.area_ha = area
        paddock.grazeable_area_ha = grazeable
    db.session.commit()

    _adjust(stock, "birth", quantity)
    db.session.commit()

    # Fixed camp IDs make the equal-remainder winner deterministic.
    _assert_counts(stock, expected)


@pytest.mark.parametrize("event_type, expected", [
    ("birth", (1, 11, 24)),
    ("sale", (1, 7, 16)),
])
def test_all_current_paddocks_keeps_existing_weighting_with_open_gates(app, event_type, expected):
    stock = _stock_in_camps()

    _adjust(stock, event_type, 6, selected=False)
    db.session.commit()

    _assert_counts(stock, expected)


def test_connected_but_unassigned_camp_is_not_a_valid_selected_camp(app):
    stock = _stock_in_camps(kids=(10, 0, 20), adults=(5, 0, 11))
    before = _database_snapshot()

    with pytest.raises(ValueError, match="not currently assigned"):
        StockService.adjust_group_stock(
            mob_id=stock.mob.id, farm_id=stock.mob.farm_id,
            animal_group_type_id=stock.kid.id, event_type=StockEventType.birth,
            quantity=4, allocation_paddock_id=str(stock.connected.id), event_time=ADJUSTED_AT,
        )
    db.session.rollback()

    assert _database_snapshot() == before


@pytest.mark.parametrize("event_type, quantity, expected", [
    ("birth", "4", (2, 12, 20)),
    ("sale", "3", (1, 6, 20)),
    ("count", "34", (2, 12, 20)),
    ("count", "27", (1, 6, 20)),
])
def test_detailed_mob_adjust_form_uses_selected_gate_pool(client, app, event_type, quantity, expected):
    stock = _stock_in_camps()

    response = client.post(f"/mobs/{stock.mob.id}/adjust", data={
        "species": "Goat", "breed": "Angora", "sex": "mixed", "age_class": "kid",
        "event_type": event_type, "quantity": quantity,
        "allocation_paddock_id": str(stock.selected.id),
    })

    assert response.status_code == 302
    _assert_counts(stock, expected)
    assert MobEvent.query.filter_by(mob_id=stock.mob.id).count() == 1


@pytest.mark.parametrize("quantity, expected, selected_cohort_count", [
    ("22", (2, 12, 20), 22),
    ("15", (1, 6, 20), 15),
])
def test_cohort_count_form_keeps_cohort_total_semantics_and_uses_gate_pool(
    client, app, quantity, expected, selected_cohort_count,
):
    stock = _stock_in_camps()
    selected_balance, other_balance = stock.balances

    response = client.post(f"/mobs/{stock.mob.id}/cohorts/adjust", data={
        "balance_id": str(selected_balance.id), "event_type": "count", "quantity": quantity,
        "allocation_paddock_id": str(stock.selected.id),
    })

    assert response.status_code == 302
    _assert_counts(stock, expected)
    assert selected_balance.head_count == selected_cohort_count
    assert other_balance.head_count == 12


@pytest.mark.parametrize("field_name", ["allocation_paddock_id", "paddock_id"])
@pytest.mark.parametrize("quantity, expected", [(34, (2, 12, 20)), (27, (1, 6, 20))])
def test_rest_count_uses_connected_camps_with_existing_selection_fields(client, app, field_name, quantity, expected):
    stock = _stock_in_camps()

    response = client.post(f"/api/mobs/{stock.mob.id}/animal-groups/adjust", json={
        "animal_group_type_id": str(stock.kid.id), "event_type": "count", "quantity": quantity,
        field_name: str(stock.selected.id),
    })

    assert response.status_code == 201
    assert response.get_json()["ledger_ids"]
    _assert_counts(stock, expected)


def test_rest_rejection_reports_pooled_availability_and_preserves_all_records(client, app):
    stock = _stock_in_camps()
    before = _database_snapshot()

    response = client.post(f"/api/mobs/{stock.mob.id}/animal-groups/adjust", json={
        "animal_group_type_id": str(stock.kid.id), "event_type": "sale", "quantity": 11,
        "allocation_paddock_id": str(stock.selected.id),
    })

    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "10 available" in error and "11 requested" in error
    assert _database_snapshot() == before


@pytest.mark.parametrize("quantity, expected", [(34, (2, 12, 20)), (27, (1, 6, 20))])
def test_authenticated_mobile_count_respects_gate_pool_and_replay(client, app, quantity, expected):
    stock = _stock_in_camps()
    user = User(email="gate-adjustments@example.com", name="Farm manager", active=True)
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=stock.home.id, role="manager"))
    db.session.commit()
    login = client.post("/api/mobile/v1/auth/login", json={
        "email": user.email, "password": "test-password", "device_name": "Test field phone",
    })
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.get_json()['token']}"}
    body = {"commands": [{
        "client_command_id": "gate-pool-count",
        "type": "stock_count.record",
        "farm_id": str(stock.home.id),
        "payload": {
            "mob_id": str(stock.mob.id), "animal_group_type_id": str(stock.kid.id),
            "quantity": quantity, "allocation_paddock_id": str(stock.selected.id),
        },
    }]}

    response = client.post("/api/mobile/v1/sync/commands", headers=headers, json=body)

    assert response.status_code == 200
    assert response.get_json()["results"][0]["status"] == "applied"
    _assert_counts(stock, expected)
    after_first_command = _database_snapshot()
    replay = client.post("/api/mobile/v1/sync/commands", headers=headers, json=body)
    assert replay.status_code == 200
    assert _database_snapshot() == after_first_command
