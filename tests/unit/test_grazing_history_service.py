from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    Paddock,
)
from app.models.stock_ledger import StockEventType
from app.services.grazing_history_service import GrazingHistoryService
from app.services.grazing_service import GrazingService
from app.services.stock_service import StockService


def _build_base_entities():
    farm = Farm(name="Unit Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    paddock = Paddock(farm_id=farm.id, name="Unit Camp", area_ha=12, grazeable_area_ha=12)
    mob = Mob(farm_id=farm.id, name="Unit Mob", status="active")
    db.session.add_all([paddock, mob])
    db.session.flush()
    return farm, paddock, mob


def test_mob_lsu_intervals_from_ledger_builds_expected_totals(app):
    with app.app_context():
        farm, _paddock, mob = _build_base_entities()
        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.purchase,
            quantity=8,
            event_time=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
        )
        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.sale,
            quantity=2,
            event_time=datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        timeline = GrazingHistoryService.mob_lsu_intervals_from_ledger(mob)
        assert timeline["coverage_start"] == datetime(2026, 1, 1, 8, 0)
        assert len(timeline["intervals"]) == 2
        assert timeline["intervals"][0]["start"] == datetime(2026, 1, 1, 8, 0)
        assert timeline["intervals"][0]["end"] == datetime(2026, 1, 3, 8, 0)
        assert round(timeline["intervals"][0]["mob_total_lsu"], 2) == 1.00
        assert round(timeline["intervals"][1]["mob_total_lsu"], 2) == 0.75


def test_sync_live_history_for_mob_rolls_forward_open_interval(app):
    with app.app_context():
        farm, paddock, mob = _build_base_entities()
        group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        db.session.add(group)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=6,
            )
        )
        GrazingService.open_session(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
            allocations=[{"paddock_id": str(paddock.id), "allocation_fraction": "1.0"}],
        )
        db.session.flush()

        initial_rows = GrazingAllocationLsuHistory.query.filter_by(paddock_id=paddock.id).all()
        assert len(initial_rows) == 1
        assert float(initial_rows[0].allocated_lsu) == 6.0

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.adjustment_in,
            quantity=2,
            event_time=datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        rows = (
            GrazingAllocationLsuHistory.query.filter_by(paddock_id=paddock.id)
            .order_by(GrazingAllocationLsuHistory.effective_from.asc())
            .all()
        )
        assert len(rows) == 2
        assert rows[0].effective_to == rows[1].effective_from
        assert float(rows[0].allocated_lsu) == 6.0
        assert float(rows[1].allocated_lsu) == 8.0


def test_backfill_from_ledger_intersects_sessions(app):
    with app.app_context():
        farm, paddock, mob = _build_base_entities()
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 6, 8, 0, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction=1,
        )
        db.session.add(allocation)

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.purchase,
            quantity=12,
            event_time=datetime(2026, 1, 3, 7, 0, tzinfo=timezone.utc),
            sync_grazing_history=False,
        )
        db.session.commit()

        summary = GrazingHistoryService.backfill_all_from_ledger()
        db.session.commit()
        rows = GrazingAllocationLsuHistory.query.filter_by(grazing_allocation_id=allocation.id).all()
        assert summary["rows_created"] >= 1
        assert len(rows) == 1
        assert rows[0].effective_from == datetime(2026, 1, 3, 7, 0)
        assert rows[0].effective_to == datetime(2026, 1, 6, 8, 0)


def test_build_paddock_daily_metrics_resets_pressure_and_includes_lsu_per_ha(app):
    with app.app_context():
        farm, paddock, mob = _build_base_entities()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2025, 12, 31, 0, 0, tzinfo=timezone.utc),
            end_at=None,
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction=1,
        )
        db.session.add(allocation)
        db.session.flush()
        db.session.add(
            GrazingAllocationLsuHistory(
                farm_id=farm.id,
                mob_id=mob.id,
                paddock_id=paddock.id,
                grazing_session_id=session.id,
                grazing_allocation_id=allocation.id,
                effective_from=datetime(2025, 12, 31, 0, 0, tzinfo=timezone.utc),
                effective_to=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                allocation_fraction=1,
                mob_total_lsu=2,
                allocated_lsu=2,
                source=GrazingHistoryService.SOURCE_BACKFILL,
            )
        )
        db.session.commit()

        payload = GrazingHistoryService.build_paddock_daily_metrics(
            [paddock],
            start_date=date(2025, 12, 31),
            end_date=date(2026, 1, 1),
        )
        rows = payload["paddocks"][str(paddock.id)]["daily"]
        assert len(rows) == 2
        assert rows[1]["pressure_pct"] < rows[0]["pressure_pct"]
        assert rows[1]["intensity_ratio"] == 0.5
        assert rows[1]["state"] == "grazed"
        assert rows[0]["lsu_per_ha"] == 0.1667
        assert rows[1]["lsu_per_ha"] == 0.0
        assert payload["paddocks"][str(paddock.id)]["metrics"]["lsu_per_ha"] == [0.1667, 0.0]
