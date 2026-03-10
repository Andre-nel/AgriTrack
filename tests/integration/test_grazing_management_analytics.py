from datetime import datetime, timedelta, timezone

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
from app.services.grazing_service import GrazingService
from app.services.stock_service import StockService


def _create_farm_with_paddocks():
    farm = Farm(name="Grazing Analytics Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North 1", area_ha=12, grazeable_area_ha=10)
    south = Paddock(farm_id=farm.id, name="South 2", area_ha=9, grazeable_area_ha=8)
    db.session.add_all([north, south])
    db.session.flush()
    return farm, north, south


def test_grazing_management_page_renders_default_history_and_timeline(client, app):
    with app.app_context():
        farm, north, south = _create_farm_with_paddocks()
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        db.session.add(mob)
        db.session.flush()
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
            start_at=datetime(2026, 3, 4, 8, 0, tzinfo=timezone.utc),
            allocations=[{"paddock_id": str(north.id), "allocation_fraction": "1.0"}],
        )
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(
        f"/analytics/grazing-management?farm_id={farm_id}&start_date=2026-03-01&end_date=2026-03-09"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Grazing Management" in body
    assert "North 1" in body
    assert "South 2" in body
    assert "Selected Period" in body
    assert "Current LSU" in body
    assert "grazing-period-button" in body


def test_grazing_management_supports_metric_filter_and_paddock_split(client, app):
    with app.app_context():
        farm, north, _south = _create_farm_with_paddocks()
        mob = Mob(farm_id=farm.id, name="Pressure Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        group = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add(group)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=4,
            )
        )
        session = GrazingService.open_session(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            allocations=[{"paddock_id": str(north.id), "allocation_fraction": "1.0"}],
        )
        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.adjustment_in,
            quantity=2,
            note="seasonal increase",
            event_time=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
        )
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)
        assert session is not None

    response = client.get(
        f"/analytics/grazing-management?farm_id={farm_id}&paddock_id={north_id}"
        "&metric=current_lsu&metric=lsu_per_ha&metric=pressure_pct&split_mode=paddock&start_date=2026-01-01&end_date=2026-01-10"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Pressure (%)" in body
    assert "Current LSU" in body
    assert "LSU/ha" in body
    assert "Split By Paddock" in body


def test_grazing_management_point_filter_limits_visible_paddocks(client, app):
    with app.app_context():
        farm, north, south = _create_farm_with_paddocks()
        mob = Mob(farm_id=farm.id, name="Filter Mob", status="active")
        db.session.add(mob)
        db.session.flush()
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
            start_at=datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc),
            allocations=[{"paddock_id": str(north.id), "allocation_fraction": "1.0"}],
        )
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(
        f"/analytics/grazing-management?farm_id={farm_id}&metric=current_lsu"
        "&point_filter_metric=current_lsu&point_filter_operator=gt&point_filter_match_mode=has_any"
        "&point_filter_value=0&start_date=2026-03-01&end_date=2026-03-09"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'class="grazing-timeline-label">North 1</div>' in body
    assert 'class="grazing-timeline-label">South 2</div>' not in body
    assert "Showing 1 of 2 paddock(s) with any matching points" in body

    response = client.get(
        f"/analytics/grazing-management?farm_id={farm_id}&metric=current_lsu"
        "&point_filter_metric=current_lsu&point_filter_operator=gt&point_filter_match_mode=has_none"
        "&point_filter_value=0&start_date=2026-03-01&end_date=2026-03-09"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'class="grazing-timeline-label">South 2</div>' in body
    assert 'class="grazing-timeline-label">North 1</div>' not in body
    assert "Showing 1 of 2 paddock(s) with no matching points" in body


def test_balance_reclassification_updates_grazing_history_once(client, app):
    with app.app_context():
        farm, north, _south = _create_farm_with_paddocks()
        mob = Mob(farm_id=farm.id, name="History Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="adult")
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
            allocations=[{"paddock_id": str(north.id), "allocation_fraction": "1.0"}],
        )
        db.session.commit()
        mob_id = str(mob.id)
        source_group_id = str(group.id)
        paddock_id = str(north.id)

    response = client.post(
        f"/mobs/{mob_id}/balances/edit",
        data={
            "source_animal_group_type_id": source_group_id,
            "sex": "wether",
            "age_class": "young",
            "head_count": "6",
            "note": "Adjusted classification while grazing",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        rows = (
            GrazingAllocationLsuHistory.query.filter_by(paddock_id=paddock_id)
            .order_by(GrazingAllocationLsuHistory.effective_from.asc())
            .all()
        )
        assert len(rows) == 2
        assert rows[0].effective_to == rows[1].effective_from
        assert float(rows[0].allocated_lsu) != float(rows[1].allocated_lsu)


def test_backfill_command_rebuilds_legacy_history_and_page_shows_warning(client, app):
    with app.app_context():
        farm, north, _south = _create_farm_with_paddocks()
        mob = Mob(farm_id=farm.id, name="Legacy Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
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
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=north.id,
                allocation_fraction=1,
            )
        )
        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=farm.id,
            animal_group_type_id=group.id,
            event_type=StockEventType.purchase,
            quantity=12,
            note="legacy opening purchase",
            event_time=datetime(2026, 1, 3, 7, 0, tzinfo=timezone.utc),
            sync_grazing_history=False,
        )
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["backfill-grazing-lsu-history"])
    assert result.exit_code == 0
    assert "Backfill complete" in result.output

    with app.app_context():
        rows = GrazingAllocationLsuHistory.query.filter_by(paddock_id=north_id).all()
        assert rows
        assert {row.source for row in rows} == {"backfill_ledger"}

    response = client.get(
        f"/analytics/grazing-management?farm_id={farm_id}&paddock_id={north_id}"
        "&start_date=2026-01-01&end_date=2026-01-06"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "unavailable" in body
    assert "North 1" in body


def test_paddock_detail_pressure_uses_persisted_grazing_history(client, app):
    with app.app_context():
        farm = Farm(
            name="Pressure Consistency Farm",
            timezone="UTC",
            default_stocking_rate_ha_per_lsu=10,
        )
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="Pressure Camp", area_ha=10, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.flush()
        mob = Mob(farm_id=farm.id, name="Pressure Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        db.session.add(group)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=10,
            )
        )
        GrazingService.open_session(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
            allocations=[{"paddock_id": str(paddock.id), "allocation_fraction": "1.0"}],
        )
        GrazingService.close_open_session(
            mob_id=mob.id,
            end_at=datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc),
        )
        db.session.commit()
        paddock_id = str(paddock.id)

    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "SDH Used This Year" in body
    assert ">2.00<" in body
