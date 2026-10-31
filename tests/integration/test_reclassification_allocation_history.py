"""Reclassification changes composition while preserving paddock heads and history."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    Paddock,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.grazing_history_service import GrazingHistoryService
from app.services.grazing_service import GrazingService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


START = datetime(2026, 6, 1, 8, tzinfo=timezone.utc)
RECLASSIFIED = START + timedelta(days=3)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _layout(*, legacy=False, target_age="old"):
    farm = Farm(name="Reclassification farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    mob = Mob(farm_id=farm.id, name="Reclassification mob", status="active")
    source = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
    target = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class=target_age)
    unrelated = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="adult")
    paddocks = {
        name: Paddock(farm_id=farm.id, name=name, area_ha=10, grazeable_area_ha=10)
        for name in ("North", "South")
    }
    db.session.add_all([mob, source, target, unrelated, *paddocks.values()])
    db.session.flush()
    counts = {"North": {source.id: 70, target.id: 5, unrelated.id: 9},
              "South": {source.id: 30, target.id: 7, unrelated.id: 11}}
    for group, count in ((source, 1 if legacy else 100), (target, 12), (unrelated, 20)):
        if legacy and group != source:
            continue
        db.session.add(AnimalGroupBalance(
            mob_id=mob.id, animal_group_type_id=group.id, head_count=count,
        ))
    db.session.flush()
    if legacy:
        session = GrazingService.open_session(
            farm.id, mob.id, START,
            [{"paddock_id": paddock.id, "allocation_fraction": "0.5"}
             for paddock in paddocks.values()],
        )
    else:
        session = MovementService.move_mob(
            mob,
            [{"paddock_id": paddocks[name].id,
              "group_counts": [{"animal_group_type_id": group_id, "head_count": count}
                               for group_id, count in group_counts.items()]}
             for name, group_counts in counts.items()],
            allocation_mode="counts", when=START,
        )
    db.session.commit()
    return SimpleNamespace(
        mob=mob, source=source, target=target, unrelated=unrelated,
        paddocks=paddocks, session=session,
    )


def _counts(session):
    return {
        allocation.paddock.name: {
            row.animal_group_type_id: row.head_count for row in allocation.group_assignments
        }
        for allocation in session.allocations
    }


def _history_snapshot(data):
    return {
        model: {row.id: (row.grazing_allocation_id, row.effective_from, row.allocated_lsu)
                for row in model.query.filter_by(mob_id=data.mob.id).all()}
        for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory)
    }


def _reclassify(data, quantity, *, when=RECLASSIFIED):
    return StockService.reclassify_group_stock(
        data.mob, data.source.id, data.target, quantity=quantity, event_time=when,
    )


@pytest.mark.parametrize("quantity", [26, 100], ids=["partial", "all"])
@pytest.mark.parametrize("target_age", ["old", "young"], ids=["same-lsu", "changed-lsu"])
def test_reclassification_conserves_each_paddock_and_preserves_history(app, quantity, target_age):
    data = _layout(target_age=target_age)
    original_counts = _counts(data.session)
    allocation_ids = {row.id for row in data.session.allocations}
    before = _history_snapshot(data)

    entries = _reclassify(data, quantity)
    db.session.commit()

    assert data.session.end_at is None
    assert {row.id for row in data.session.allocations} == allocation_ids
    counts = _counts(data.session)
    transferred = {"North": 18, "South": 8} if quantity == 26 else {"North": 70, "South": 30}
    for name, old_counts in original_counts.items():
        assert sum(counts[name].values()) == sum(old_counts.values())
        assert counts[name].get(data.source.id, 0) == old_counts[data.source.id] - transferred[name]
        assert counts[name][data.target.id] == old_counts[data.target.id] + transferred[name]
        assert counts[name][data.unrelated.id] == old_counts[data.unrelated.id]
    assert StockService.group_head_count(data.mob.id, data.source.id) == 100 - quantity
    assert StockService.group_head_count(data.mob.id, data.target.id) == 12 + quantity
    assert sum(row.quantity for row in entries if row.event_type == StockEventType.adjustment_out) == quantity
    assert sum(row.quantity for row in entries if row.event_type == StockEventType.adjustment_in) == quantity
    assert all(_utc(row.event_time) == RECLASSIFIED for row in entries)

    for model, rows in before.items():
        for row_id, original in rows.items():
            row = db.session.get(model, row_id)
            assert (row.grazing_allocation_id, row.effective_from, row.allocated_lsu) == original
            changed = model == GrazingAllocationLsuHistory and target_age != "old"
            changed = changed or (model == GrazingAllocationLsuBreakdownHistory
                                  and row.animal_group_type_id != data.unrelated.id)
            assert (_utc(row.effective_to) if row.effective_to else None) == (RECLASSIFIED if changed else None)

    aggregate = GrazingAllocationLsuHistory.query.filter_by(mob_id=data.mob.id).all()
    assert len(aggregate) == (2 if target_age == "old" else 4)
    current_breakdown = GrazingAllocationLsuBreakdownHistory.query.filter_by(
        mob_id=data.mob.id, effective_to=None,
    ).all()
    assert {(row.paddock.name, row.animal_group_type_id): int(row.allocated_head_count)
            for row in current_breakdown} == {
        (name, group_id): count for name, group_counts in counts.items()
        for group_id, count in group_counts.items()
    }
    assert all(_utc(row.effective_from) == RECLASSIFIED for row in current_breakdown
               if row.animal_group_type_id != data.unrelated.id)


def test_legacy_rounding_closes_session_without_deleting_half_head_history(app):
    data = _layout(legacy=True)
    old_allocation_ids = {row.id for row in data.session.allocations}
    before = _history_snapshot(data)
    days_before = {
        name: GrazingHistoryService.paddock_lsu_days_for_period(
            paddock, period_start=START, period_end=RECLASSIFIED,
        ) for name, paddock in data.paddocks.items()
    }

    _reclassify(data, 1)
    db.session.commit()

    current = GrazingSession.query.filter_by(mob_id=data.mob.id, end_at=None).one()
    assert current.id != data.session.id
    assert _utc(data.session.end_at) == _utc(current.start_at) == RECLASSIFIED
    assert {row.id for row in data.session.allocations} == old_allocation_ids
    assert _counts(data.session) == {"North": {}, "South": {}}
    assert len(current.allocations) == 1
    assert list(_counts(current).values()) == [{data.target.id: 1}]
    for model, rows in before.items():
        for row_id, original in rows.items():
            row = db.session.get(model, row_id)
            assert row is not None
            assert (row.grazing_allocation_id, row.effective_from, row.allocated_lsu) == original
            assert _utc(row.effective_to) == RECLASSIFIED
            if model == GrazingAllocationLsuBreakdownHistory:
                assert float(row.allocated_head_count) == 0.5
    for name, paddock in data.paddocks.items():
        assert GrazingHistoryService.paddock_lsu_days_for_period(
            paddock, period_start=START, period_end=RECLASSIFIED,
        ) == pytest.approx(days_before[name])
    current_rows = GrazingAllocationLsuBreakdownHistory.query.filter_by(
        mob_id=data.mob.id, effective_to=None,
    ).all()
    assert len(current_rows) == 1
    assert current_rows[0].animal_group_type_id == data.target.id
    assert current_rows[0].allocated_head_count == 1
    assert _utc(current_rows[0].effective_from) == RECLASSIFIED


def test_failed_posting_rolls_back_legacy_session_rotation_and_history(app, monkeypatch):
    data = _layout(legacy=True)
    before = _history_snapshot(data)
    cohort_count = AnimalCohort.query.count()
    original_post = StockService.adjust_stock

    def fail_incoming(**kwargs):
        if kwargs["event_type"] == StockEventType.adjustment_in:
            raise RuntimeError("incoming posting failed")
        return original_post(**kwargs)

    monkeypatch.setattr(StockService, "adjust_stock", staticmethod(fail_incoming))
    with pytest.raises(RuntimeError, match="incoming posting failed"):
        with db.session.begin_nested():
            _reclassify(data, 1)
    db.session.expire_all()

    assert data.session.end_at is None
    assert GrazingSession.query.filter_by(mob_id=data.mob.id).count() == 1
    assert GrazingAllocation.query.count() == 2
    assert _counts(data.session) == {"North": {}, "South": {}}
    assert _history_snapshot(data) == before
    assert all(model.query.filter_by(effective_to=None).count() == 2 for model in before)
    assert StockService.group_head_count(data.mob.id, data.source.id) == 1
    assert StockService.group_head_count(data.mob.id, data.target.id) == 0
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohort.query.count() == cohort_count


@pytest.mark.parametrize("later_history", [False, True], ids=["before-session", "before-history"])
def test_legacy_reclassification_rejects_time_before_existing_history(app, later_history):
    data = _layout(legacy=True)
    when = START - timedelta(days=1)
    if later_history:
        # A legacy session may have live coverage only from a later capture.
        for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory):
            for row in model.query.all():
                row.effective_from = START + timedelta(days=2)
        db.session.commit()
        when = START + timedelta(days=1)
    before = _history_snapshot(data)

    with pytest.raises(ValueError, match="precedes the current grazing allocation history"):
        with db.session.begin_nested():
            _reclassify(data, 1, when=when)
    db.session.expire_all()

    assert data.session.end_at is None
    assert GrazingSession.query.count() == 1
    assert GrazingAllocation.query.count() == 2
    assert _history_snapshot(data) == before
    assert all(model.query.filter_by(effective_to=None).count() == 2 for model in before)
    assert StockService.group_head_count(data.mob.id, data.source.id) == 1
    assert StockLedgerEntry.query.count() == 0
