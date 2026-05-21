from datetime import date
from pathlib import Path

from app.extensions import db
from app.models import (
    CalendarActivity,
    CashTransaction,
    Farm,
    JournalEntry,
    Mob,
    Paddock,
    RainfallRecord,
    Task,
    TaskEntityLink,
    TaskSpace,
    WaterAsset,
    WaterAssetServedPaddock,
    WaterConnection,
)
from app.services.task_service import TaskService


def test_manage_farms_shows_delete_action(client, app):
    with app.app_context():
        db.session.add(Farm(name="Delete Button Farm", timezone="UTC", active=True))
        db.session.commit()

    response = client.get("/farms")

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Delete Button Farm" in body
    assert "Delete" in body
    assert "/farms/" in body
    assert "/delete" in body


def test_delete_farm_removes_farm_records_and_map_file(client, app):
    instance_path = Path(app.root_path).parent / "test_instance_farm_delete"
    maps_dir = instance_path / "maps"
    map_path = maps_dir / "Delete Me Farm.kml"
    maps_dir.mkdir(parents=True, exist_ok=True)
    if map_path.exists():
        map_path.unlink()
    app.instance_path = str(instance_path)
    with app.app_context():
        farm = Farm(name="Delete Me Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(
            farm_id=farm.id,
            name="North",
            area_ha=10,
            grazeable_area_ha=8,
        )
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        rainfall = RainfallRecord(farm_id=farm.id, recorded_on=date(2026, 1, 5), mm=12)
        journal_entry = JournalEntry(farm_id=farm.id, description="Farm note")
        calendar_activity = CalendarActivity(
            farm_id=farm.id,
            title="Inspect fences",
            start_date=date(2026, 1, 6),
            duration_days=1,
        )
        task_space = TaskSpace(
            farm_id=farm.id,
            key="DEL",
            name="Delete Me",
            description="Farm tasks",
        )
        cash_transaction = CashTransaction(
            farm_id=farm.id,
            transaction_date=date(2026, 1, 7),
            description="Farm expense",
        )
        db.session.add_all(
            [
                paddock,
                mob,
                rainfall,
                journal_entry,
                calendar_activity,
                task_space,
                cash_transaction,
            ]
        )
        db.session.flush()

        tank = WaterAsset(
            farm_id=farm.id,
            name="Tank",
            asset_type="tank",
            active=True,
            location_paddock_id=paddock.id,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough",
            asset_type="trough",
            active=True,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([tank, trough])
        db.session.flush()

        db.session.add_all(
            [
                WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id),
                WaterConnection(
                    farm_id=farm.id,
                    flow_type="gravity",
                    source_asset_id=tank.id,
                    destination_asset_id=trough.id,
                ),
            ]
        )
        task = TaskService.create_task(
            space=task_space,
            heading="Delete linked task",
            description="Task with farm entity links",
            raw_tags="cleanup",
            reporter_name="Ava",
            assignee_name="Noah",
            status="todo",
            priority="low",
            original_estimate_days="",
            due_date="",
        )
        TaskService.add_entity_links(
            task=task,
            paddock_ids=[str(paddock.id)],
            water_asset_ids=[str(tank.id)],
            mob_ids=[str(mob.id)],
        )
        db.session.commit()

        farm_id = farm.id
        paddock_id = paddock.id
        mob_id = mob.id
        cash_transaction_id = cash_transaction.id

    map_path.write_text("<kml />", encoding="utf-8")

    response = client.post(f"/farms/{farm_id}/delete", follow_redirects=True)

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Delete Me Farm deleted" in body
    assert "Delete Me Farm" not in body.replace("Delete Me Farm deleted", "")
    assert not map_path.exists()

    with app.app_context():
        assert db.session.get(Farm, farm_id) is None
        assert db.session.get(Paddock, paddock_id) is None
        assert db.session.get(Mob, mob_id) is None
        assert RainfallRecord.query.filter_by(farm_id=farm_id).count() == 0
        assert JournalEntry.query.filter_by(farm_id=farm_id).count() == 0
        assert CalendarActivity.query.filter_by(farm_id=farm_id).count() == 0
        assert TaskSpace.query.filter_by(farm_id=farm_id).count() == 0
        assert Task.query.count() == 0
        assert TaskEntityLink.query.count() == 0
        assert WaterAsset.query.filter_by(farm_id=farm_id).count() == 0
        assert WaterConnection.query.filter_by(farm_id=farm_id).count() == 0
        assert db.session.get(CashTransaction, cash_transaction_id).farm_id is None

    try:
        maps_dir.rmdir()
        instance_path.rmdir()
    except OSError:
        pass
