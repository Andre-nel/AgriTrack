from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models import Farm, Paddock
from app.models.farm_capacity import FarmCapacityHistory
from app.services.farm_capacity_service import FarmCapacityService


def test_calculate_respects_each_paddocks_effective_area_rate_and_status():
    farm = Farm(default_stocking_rate_ha_per_lsu=Decimal("6"))
    paddocks = [
        Paddock(status="active", area_ha=120, grazeable_area_ha=60),
        Paddock(status="active", area_ha=30, grazeable_area_ha=0),
        Paddock(
            status="active", area_ha=100, grazeable_area_ha=40,
            stocking_rate_ha_per_lsu_override=Decimal("4"),
        ),
        Paddock(status="inactive", area_ha=600, grazeable_area_ha=600),
    ]
    assert FarmCapacityService.calculate(farm, paddocks) == Decimal("25")
    assert FarmCapacityService.calculate(farm, []) == Decimal("0")


def test_record_empty_creation_changes_and_noops(app):
    farm = Farm(name="Recorded capacity", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    baseline = FarmCapacityService.record(farm, source="created")
    assert baseline.capacity_lsu == Decimal("0.000000")
    assert baseline.source == "created"
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=30, grazeable_area_ha=0)
    db.session.add(paddock)
    added = FarmCapacityService.record(farm.id)
    assert added.capacity_lsu == Decimal("5.000000")
    assert FarmCapacityService.record(farm) is None

    paddock.name = "Renamed"
    assert FarmCapacityService.record(farm) is None
    paddock.stocking_rate_ha_per_lsu_override = 3
    assert FarmCapacityService.record(farm).capacity_lsu == Decimal("10.000000")
    farm.default_stocking_rate_ha_per_lsu = 10
    assert FarmCapacityService.record(farm) is None
    paddock.stocking_rate_ha_per_lsu_override = None
    assert FarmCapacityService.record(farm).capacity_lsu == Decimal("3.000000")
    paddock.status = "inactive"
    assert FarmCapacityService.record(farm).capacity_lsu == Decimal("0.000000")
    assert FarmCapacityHistory.query.count() == 5


def test_record_uses_persisted_numeric_precision(app):
    farm = Farm(name="Precision", default_stocking_rate_ha_per_lsu=Decimal("6.126"))
    db.session.add(farm)
    db.session.flush()
    db.session.add(Paddock(farm_id=farm.id, name="North", area_ha=Decimal("12.346")))
    snapshot = FarmCapacityService.record(farm, source="created")
    expected = (Decimal("12.35") / Decimal("6.13")).quantize(Decimal("0.000001"))
    assert snapshot.capacity_lsu == expected
    db.session.expire_all()
    assert FarmCapacityService.record(farm) is None


def test_snapshot_and_capacity_edit_rollback_together(app):
    farm = Farm(name="Rollback", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    FarmCapacityService.record(farm, source="created")
    db.session.commit()
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=60)
    db.session.add(paddock)
    FarmCapacityService.record(farm)
    db.session.rollback()
    assert Paddock.query.count() == 0
    assert FarmCapacityHistory.query.one().capacity_lsu == Decimal("0.000000")


def test_simultaneous_changes_have_deterministic_order_and_utc_time(app):
    farm = Farm(name="Simultaneous", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    when = datetime(2026, 10, 12, 12, tzinfo=timezone(timedelta(hours=2)))
    first = FarmCapacityService.record(farm, source="created", effective_at=when)
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=60)
    db.session.add(paddock)
    second = FarmCapacityService.record(farm, effective_at=when)
    assert first.id < second.id
    assert first.effective_at == second.effective_at == datetime(2026, 10, 12, 10)
    assert FarmCapacityService.record(farm, effective_at=when) is None
