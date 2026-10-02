"""Stock changes must preserve grazing evidence when an allocation becomes empty."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    MovementEvent,
    Paddock,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.modules.mobs.presenters import build_mob_detail_context
from app.services.cohort_service import CohortService
from app.services.gate_service import GateService
from app.services.grazing_history_service import GrazingHistoryService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


START = datetime(2026, 6, 1, 8, tzinfo=timezone.utc)
ADJUSTED = START + timedelta(days=3)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _layout(counts, *, multiple_cohorts=False):
    """Create exact counts without redistributing them when preparing the open gate."""
    farm = Farm(name="Stock history farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    mob = Mob(farm_id=farm.id, name="History mob", status="active")
    groups = {
        "kid": AnimalGroupType(species="Goat", breed="Angora", sex="mixed", age_class="kid"),
        "adult": AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult"),
    }
    paddocks = {
        name: Paddock(farm_id=farm.id, name=name, area_ha=10, grazeable_area_ha=10)
        for name in ("North", "South", "Outside")
    }
    db.session.add_all([mob, *groups.values(), *paddocks.values()])
    db.session.flush()
    for name, group in groups.items():
        total = sum(row.get(name, 0) for row in counts.values())
        if not total:
            continue
        portions = [total // 2, total - total // 2] if multiple_cohorts else [total]
        for portion in portions:
            cohort = CohortService.create_cohort(
                animal_group_type_id=group.id, origin_farm_id=farm.id,
            )
            db.session.add(AnimalGroupBalance(
                mob_id=mob.id, animal_group_type_id=group.id,
                cohort_id=cohort.id, head_count=portion,
            ))
    db.session.flush()
    original = MovementService.move_mob(
        mob,
        [{
            "paddock_id": paddocks[name].id,
            "group_counts": [
                {"animal_group_type_id": groups[group].id, "head_count": quantity}
                for group, quantity in row.items()
            ],
        } for name, row in counts.items()],
        allocation_mode="counts", when=START,
    )
    gate = GateService.create_manual_gate(
        farm_id=farm.id, paddock_a_id=paddocks["North"].id,
        paddock_b_id=paddocks["South"].id,
    )
    # The adjustment operates on an already-open gate and an observed allocation.
    gate.status = "open"
    db.session.commit()
    return SimpleNamespace(farm=farm, mob=mob, groups=groups, paddocks=paddocks, original=original)


def _counts(session):
    return {
        allocation.paddock.name: {
            assignment.animal_group_type.age_class: assignment.head_count
            for assignment in allocation.group_assignments
        }
        for allocation in session.allocations
    }


def _active(data):
    return GrazingSession.query.filter_by(mob_id=data.mob.id, end_at=None).one_or_none()


def _capture_history(data):
    return {
        "allocations": {row.id for row in data.original.allocations},
        "counts": _counts(data.original),
        "aggregate": {
            row.id: (row.grazing_allocation_id, row.allocated_lsu, row.mob_total_lsu)
            for row in data.original.lsu_history
        },
        "breakdown": {
            row.id: (row.grazing_allocation_id, row.animal_group_type_id, row.allocated_head_count)
            for row in data.original.lsu_breakdown_history
        },
        "lsu_days": {
            name: GrazingHistoryService.paddock_lsu_days_for_period(
                paddock, period_start=START, period_end=ADJUSTED,
            )
            for name, paddock in data.paddocks.items()
        },
    }


def _assert_closed_history_preserved(data, before):
    assert _utc(data.original.end_at) == ADJUSTED
    assert {row.id for row in data.original.allocations} == before["allocations"]
    assert _counts(data.original) == before["counts"]
    assert before["aggregate"] and before["breakdown"]
    for row_id, values in before["aggregate"].items():
        row = db.session.get(GrazingAllocationLsuHistory, row_id)
        assert row is not None
        assert (row.grazing_allocation_id, row.allocated_lsu, row.mob_total_lsu) == values
        assert _utc(row.effective_from) == START
        assert _utc(row.effective_to) == ADJUSTED
    for row_id, values in before["breakdown"].items():
        row = db.session.get(GrazingAllocationLsuBreakdownHistory, row_id)
        assert row is not None
        assert (row.grazing_allocation_id, row.animal_group_type_id, row.allocated_head_count) == values
        assert _utc(row.effective_from) == START
        assert _utc(row.effective_to) == ADJUSTED
    for name, expected in before["lsu_days"].items():
        assert GrazingHistoryService.paddock_lsu_days_for_period(
            data.paddocks[name], period_start=START, period_end=ADJUSTED,
        ) == pytest.approx(expected)


def _adjust(data, quantity, *, aggregate=True, event=StockEventType.sale):
    method = StockService.adjust_group_stock if aggregate else StockService.adjust_stock
    return method(
        mob_id=data.mob.id, farm_id=data.farm.id,
        animal_group_type_id=data.groups["kid"].id,
        event_type=event, quantity=quantity, event_time=ADJUSTED,
        allocation_paddock_id=data.paddocks["North"].id,
    )


@pytest.mark.parametrize("aggregate", [False, True], ids=["cohort", "aggregate"])
def test_pooled_reduction_emptying_one_camp_preserves_history_and_continuous_days(app, aggregate):
    data = _layout(
        {"North": {"kid": 1}, "South": {"kid": 9}, "Outside": {"adult": 4}},
        multiple_cohorts=aggregate,
    )
    before = _capture_history(data)
    movements_before = MovementEvent.query.count()

    _adjust(data, 6, aggregate=aggregate)
    db.session.commit()

    current = _active(data)
    assert current.id != data.original.id
    assert _utc(current.start_at) == ADJUSTED
    assert _counts(current) == {"South": {"kid": 4}, "Outside": {"adult": 4}}
    _assert_closed_history_preserved(data, before)
    assert MovementEvent.query.count() == movements_before
    current_history = GrazingAllocationLsuBreakdownHistory.query.filter_by(
        grazing_session_id=current.id, effective_to=None,
    ).all()
    assert {
        (row.paddock.name, row.animal_group_type.age_class): int(row.allocated_head_count)
        for row in current_history
    } == {("South", "kid"): 4, ("Outside", "adult"): 4}
    assert all(_utc(row.effective_from) == ADJUSTED for row in current_history)
    assert all(row.head_count == 4 for row in current_history)
    context = build_mob_detail_context(data.mob, "", as_of=ADJUSTED + timedelta(days=2))
    assert {
        row["paddock_name"]: row["days_assigned_continuously"]
        for row in context["current_allocations"]
    } == {"South": 5.0, "Outside": 5.0}


def test_draining_connected_pool_keeps_disconnected_animals_and_old_allocations(app):
    data = _layout({"North": {"kid": 1}, "South": {"kid": 9}, "Outside": {"kid": 5}})
    before = _capture_history(data)

    # Count means a mob total of five, so its resulting reduction drains the pool.
    _adjust(data, 5, event=StockEventType.count)
    db.session.commit()

    assert _counts(_active(data)) == {"Outside": {"kid": 5}}
    assert StockService.group_head_count(data.mob.id, data.groups["kid"].id) == 5
    _assert_closed_history_preserved(data, before)


@pytest.mark.parametrize("aggregate", [False, True], ids=["cohort", "aggregate"])
def test_removing_entire_mob_closes_session_without_deleting_grazing_history(app, aggregate):
    data = _layout(
        {"North": {"kid": 1}, "South": {"kid": 9}}, multiple_cohorts=aggregate,
    )
    before = _capture_history(data)

    _adjust(data, 10, aggregate=aggregate)
    db.session.commit()

    assert _active(data) is None
    assert GrazingSession.query.filter_by(mob_id=data.mob.id).count() == 1
    assert AnimalGroupBalance.query.filter_by(mob_id=data.mob.id).count() == 0
    assert GrazingAllocationLsuHistory.query.filter_by(mob_id=data.mob.id, effective_to=None).count() == 0
    assert GrazingAllocationLsuBreakdownHistory.query.filter_by(mob_id=data.mob.id, effective_to=None).count() == 0
    assert sum(row.quantity for row in StockLedgerEntry.query.all()) == 10
    assert MovementEvent.query.count() == 1
    _assert_closed_history_preserved(data, before)


def test_partial_reduction_keeps_session_and_allocation_ids_with_history_boundary(app):
    data = _layout({"North": {"kid": 5}, "South": {"kid": 5}})
    allocation_ids = {row.id for row in data.original.allocations}
    old_history_ids = {row.id for row in data.original.lsu_breakdown_history}

    _adjust(data, 2)
    db.session.commit()

    assert _active(data).id == data.original.id
    assert {row.id for row in data.original.allocations} == allocation_ids
    assert _counts(data.original) == {"North": {"kid": 4}, "South": {"kid": 4}}
    for row_id in old_history_ids:
        row = db.session.get(GrazingAllocationLsuBreakdownHistory, row_id)
        assert row.allocated_head_count == 5
        assert _utc(row.effective_to) == ADJUSTED
    current_rows = GrazingAllocationLsuBreakdownHistory.query.filter_by(effective_to=None).all()
    assert len(current_rows) == 2
    assert all(row.allocated_head_count == 4 and row.head_count == 8 for row in current_rows)
    assert all(_utc(row.effective_from) == ADJUSTED for row in current_rows)


def test_failed_transaction_restores_rotated_session_balances_and_history(app):
    data = _layout({"North": {"kid": 1}, "South": {"kid": 9}})
    before = _capture_history(data)

    with pytest.raises(RuntimeError, match="later operation failed"):
        with db.session.begin_nested():
            _adjust(data, 6)
            assert _active(data).id != data.original.id
            raise RuntimeError("later operation failed")
    db.session.expire_all()

    assert _active(data).id == data.original.id
    assert data.original.end_at is None
    assert _counts(data.original) == before["counts"]
    assert {row.id for row in data.original.allocations} == before["allocations"]
    assert GrazingSession.query.filter_by(mob_id=data.mob.id).count() == 1
    assert StockService.group_head_count(data.mob.id, data.groups["kid"].id) == 10
    assert StockLedgerEntry.query.count() == 0
    assert all(db.session.get(GrazingAllocationLsuHistory, row_id).effective_to is None
               for row_id in before["aggregate"])
    assert all(db.session.get(GrazingAllocationLsuBreakdownHistory, row_id).effective_to is None
               for row_id in before["breakdown"])


@pytest.mark.parametrize("after_latest_change", [False, True], ids=["before-session", "before-history"])
@pytest.mark.parametrize("aggregate", [False, True], ids=["cohort", "aggregate"])
def test_backdated_depletion_rejects_and_rolls_back_without_rewriting_history(
    app, after_latest_change, aggregate,
):
    data = _layout({"North": {"kid": 1}, "South": {"kid": 9}})
    adjustment_time = START - timedelta(days=1)
    if after_latest_change:
        # History collections can already be loaded before another operation in
        # the transaction adds newer rows by foreign key.
        list(data.original.lsu_history)
        list(data.original.lsu_breakdown_history)
        StockService.adjust_stock(
            mob_id=data.mob.id, farm_id=data.farm.id,
            animal_group_type_id=data.groups["kid"].id,
            event_type=StockEventType.birth, quantity=2,
            allocation_paddock_id=data.paddocks["North"].id,
            event_time=START + timedelta(days=2),
        )
        db.session.flush()
        adjustment_time = START + timedelta(days=1)

    def saved_history():
        return {
            model.__name__: {
                row.id: (
                    row.grazing_allocation_id,
                    _utc(row.effective_from),
                    _utc(row.effective_to) if row.effective_to else None,
                    row.allocated_lsu,
                )
                for row in model.query.filter_by(mob_id=data.mob.id).all()
            }
            for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory)
        }

    history_before = saved_history()
    counts_before = _counts(data.original)
    allocations_before = {row.id for row in data.original.allocations}
    ledger_ids_before = {row.id for row in StockLedgerEntry.query.all()}
    quantity = StockService.group_head_count(data.mob.id, data.groups["kid"].id)
    method = StockService.adjust_group_stock if aggregate else StockService.adjust_stock

    with pytest.raises(ValueError, match="precedes the current grazing allocation history"):
        with db.session.begin_nested():
            method(
                mob_id=data.mob.id, farm_id=data.farm.id,
                animal_group_type_id=data.groups["kid"].id,
                event_type=StockEventType.sale, quantity=quantity,
                allocation_paddock_id=data.paddocks["North"].id,
                event_time=adjustment_time,
            )
    db.session.expire_all()

    assert _active(data).id == data.original.id
    assert data.original.end_at is None
    assert _counts(data.original) == counts_before
    assert {row.id for row in data.original.allocations} == allocations_before
    assert GrazingSession.query.filter_by(mob_id=data.mob.id).count() == 1
    assert StockService.group_head_count(data.mob.id, data.groups["kid"].id) == quantity
    assert {row.id for row in StockLedgerEntry.query.all()} == ledger_ids_before
    assert saved_history() == history_before


@pytest.mark.parametrize("operation", ["transfer", "split", "merge"])
def test_multitype_movement_closes_emptied_allocations_at_one_timestamp(app, operation):
    data = _layout({"North": {"kid": 2}, "South": {"adult": 4}}, multiple_cohorts=True)
    before = _capture_history(data)
    groups = [
        {"animal_group_type_id": data.groups["kid"].id, "quantity": 2},
        {"animal_group_type_id": data.groups["adult"].id, "quantity": 4},
    ]
    if operation == "transfer":
        destination = Mob(farm_id=data.farm.id, name="Destination", status="active")
        db.session.add(destination)
        db.session.flush()
        MovementService.transfer_stock_between_mobs(
            data.mob, destination, groups, when=ADJUSTED,
        )
    elif operation == "split":
        [destination] = MovementService.split_mob(
            data.mob, [{"name": "Split destination", "groups": groups}], when=ADJUSTED,
        )
    else:
        destination = MovementService.merge_mobs([data.mob], "Merged destination", when=ADJUSTED)
    db.session.commit()

    assert _active(data) is None
    assert AnimalGroupBalance.query.filter_by(mob_id=data.mob.id).count() == 0
    assert StockService.group_head_count(destination.id, data.groups["kid"].id) == 2
    assert StockService.group_head_count(destination.id, data.groups["adult"].id) == 4
    assert MovementEvent.query.count() == (1 if operation == "transfer" else 2)
    assert all(_utc(row.event_time) == ADJUSTED for row in StockLedgerEntry.query.all())
    assert all(_utc(session.end_at) == ADJUSTED for session in data.mob.grazing_sessions)
    assert GrazingAllocationLsuHistory.query.filter_by(mob_id=data.mob.id, effective_to=None).count() == 0
    _assert_closed_history_preserved(data, before)
    assert all(db.session.get(GrazingAllocation, row_id) is not None for row_id in before["allocations"])


def test_multitype_transfer_updates_destination_history_at_the_transfer_timestamp(app):
    data = _layout({"North": {"kid": 2}, "South": {"adult": 4}})
    destination = Mob(farm_id=data.farm.id, name="Occupied destination", status="active")
    db.session.add(destination)
    db.session.flush()
    db.session.add(AnimalGroupBalance(
        mob_id=destination.id, animal_group_type_id=data.groups["adult"].id, head_count=10,
    ))
    destination_session = MovementService.move_mob(
        destination,
        [{
            "paddock_id": data.paddocks["Outside"].id,
            "group_counts": [{"animal_group_type_id": data.groups["adult"].id, "head_count": 10}],
        }],
        allocation_mode="counts", when=START,
    )
    db.session.commit()
    [old_history] = destination_session.lsu_breakdown_history
    [old_allocation] = destination_session.allocations

    MovementService.transfer_stock_between_mobs(
        data.mob, destination,
        [
            {"animal_group_type_id": data.groups["kid"].id, "quantity": 2},
            {"animal_group_type_id": data.groups["adult"].id, "quantity": 4},
        ],
        when=ADJUSTED,
    )
    db.session.commit()

    assert destination_session.end_at is None
    assert [row.id for row in destination_session.allocations] == [old_allocation.id]
    assert _counts(destination_session) == {"Outside": {"kid": 2, "adult": 14}}
    assert old_history.allocated_head_count == 10
    assert _utc(old_history.effective_to) == ADJUSTED
    current_history = GrazingAllocationLsuBreakdownHistory.query.filter_by(
        mob_id=destination.id, effective_to=None,
    ).all()
    assert {
        row.animal_group_type.age_class: (row.head_count, int(row.allocated_head_count))
        for row in current_history
    } == {"kid": (2, 2), "adult": (14, 14)}
    assert all(_utc(row.effective_from) == ADJUSTED for row in current_history)
    assert MovementEvent.query.count() == 2
