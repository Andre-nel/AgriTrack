from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import (
    AnimalGroupBalance, AnimalGroupType, Farm, GrazingSession, Mob,
    MovementEvent, Paddock, StockLedgerEntry,
)
from app.models.mob_inventory import MobInventoryBaselineGroup, MobInventoryStateHistory
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.mob_inventory_service import MobInventoryService
from app.services.mob_service import MobService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


@pytest.fixture
def inventory(app):
    with app.app_context():
        first, second = Farm(name="First Inventory Farm"), Farm(name="Second Inventory Farm")
        db.session.add_all([first, second])
        db.session.flush()
        mob = Mob(farm_id=first.id, name="Inventory Mob", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        paddock = Paddock(farm_id=second.id, name="Destination", area_ha=20)
        db.session.add_all([mob, group, paddock])
        db.session.commit()
        return {"mob": mob.id, "group": group.id, "first": first.id,
                "second": second.id, "paddock": paddock.id}


def _stock(data, quantity, *, event_type=StockEventType.purchase, when=None):
    return StockService.adjust_group_stock(
        data["mob"], data["first"], data["group"], event_type, quantity,
        event_time=when, sync_grazing_history=False, sync_allocation_distribution=False,
    )


def test_baseline_combines_cohorts_and_captures_future_and_closed_group_ledger(app, inventory):
    with app.app_context():
        mob = db.session.get(Mob, inventory["mob"])
        future = datetime.now(timezone.utc) + timedelta(days=10)
        for count in (4, 7):
            cohort = CohortService.create_cohort(
                animal_group_type_id=inventory["group"], origin_farm_id=inventory["first"],
            )
            db.session.add(AnimalGroupBalance(
                mob_id=mob.id, animal_group_type_id=inventory["group"],
                cohort_id=cohort.id, head_count=count,
            ))
        closed_group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(closed_group)
        db.session.flush()
        for group_id, quantity, event_type in (
            (inventory["group"], 15, StockEventType.purchase),
            (inventory["group"], 4, StockEventType.sale),
            (closed_group.id, 3, StockEventType.transfer_out),
        ):
            db.session.add(StockLedgerEntry(
                farm_id=inventory["first"], mob_id=mob.id, animal_group_type_id=group_id,
                quantity=quantity, event_type=event_type, event_time=future,
            ))
        initial = MobInventoryService.ensure_baseline(mob)
        assert initial.source == "baseline"
        assert initial.active is True
        assert initial.previous_active is None
        assert {row.animal_group_type_id: (row.head_count, row.ledger_net)
                for row in initial.baseline_groups} == {
            inventory["group"]: (11, 11), closed_group.id: (0, -3),
        }
        assert MobInventoryService.ensure_baseline(mob).id == initial.id
        assert MobInventoryStateHistory.query.count() == 1


def test_stock_captures_before_mutation_and_rollback_removes_snapshot(app, inventory):
    with app.app_context():
        db.session.add(AnimalGroupBalance(
            mob_id=inventory["mob"], animal_group_type_id=inventory["group"], head_count=9,
        ))
        db.session.commit()
        _stock(inventory, 2, when=datetime.now(timezone.utc) - timedelta(days=3))
        row = MobInventoryBaselineGroup.query.one()
        assert (row.head_count, row.ledger_net) == (9, 0)
        assert StockService.group_head_count(inventory["mob"], inventory["group"]) == 11
        db.session.rollback()
        assert MobInventoryStateHistory.query.count() == 0
        assert MobInventoryBaselineGroup.query.count() == 0
        assert StockLedgerEntry.query.count() == 0
        assert StockService.group_head_count(inventory["mob"], inventory["group"]) == 9


def test_backdated_and_future_postings_do_not_rewrite_existing_anchor(app, inventory):
    with app.app_context():
        now = datetime.now(timezone.utc)
        _stock(inventory, 10, when=now + timedelta(days=4))
        db.session.commit()
        initial = MobInventoryStateHistory.query.one()
        assert initial.baseline_groups == []
        original_time = initial.effective_at
        _stock(inventory, 2, when=now - timedelta(days=4))
        db.session.commit()
        assert MobInventoryStateHistory.query.count() == 1
        assert initial.effective_at == original_time
        assert initial.baseline_groups == []


def test_movement_records_source_farm_and_same_time_status_has_order(app, inventory):
    with app.app_context():
        mob = db.session.get(Mob, inventory["mob"])
        when = datetime.now(timezone.utc)
        initial = MobInventoryService.ensure_baseline(mob, effective_at=when, source="created")
        MovementService.move_mob(
            mob, [{"paddock_id": inventory["paddock"], "allocation_fraction": 1}],
            destination_farm_id=inventory["second"], when=when,
        )
        MobService.archive_mob(mob, when=when)
        db.session.commit()
        rows = MobInventoryStateHistory.query.order_by(MobInventoryStateHistory.id).all()
        assert [row.source for row in rows] == ["created", "move", "status"]
        assert rows[0].id == initial.id < rows[1].id < rows[2].id
        assert rows[1].previous_farm_id == inventory["first"]
        assert rows[1].farm_id == inventory["second"]
        assert (rows[2].previous_active, rows[2].active) == (True, False)
        assert len({row.effective_at for row in rows}) == 1
        MobService.set_status(mob, "inactive", when=when)
        db.session.flush()
        assert MobInventoryStateHistory.query.count() == 3
        MobService.set_status(mob, "active", when=when)
        assert MobInventoryStateHistory.query.count() == 4


def test_intrafarm_move_does_not_record_inventory_state_change(app, inventory):
    with app.app_context():
        mob = db.session.get(Mob, inventory["mob"])
        paddock = db.session.get(Paddock, inventory["paddock"])
        paddock.farm_id = mob.farm_id
        MovementService.move_mob(
            mob, [{"paddock_id": paddock.id, "allocation_fraction": 1}],
        )
        assert [row.source for row in MobInventoryStateHistory.query.all()] == ["baseline"]


@pytest.mark.parametrize("operation", ["move", "archive"])
def test_default_state_change_time_follows_missing_baseline_capture(app, inventory, operation):
    with app.app_context():
        mob = db.session.get(Mob, inventory["mob"])
        if operation == "move":
            MovementService.move_mob(
                mob, [{"paddock_id": inventory["paddock"], "allocation_fraction": 1}],
                destination_farm_id=inventory["second"],
            )
        else:
            MobService.archive_mob(mob)
        rows = MobInventoryStateHistory.query.order_by(MobInventoryStateHistory.id).all()
        assert len(rows) == 2
        assert rows[0].source == "baseline"
        assert rows[0].effective_at <= rows[1].effective_at


def test_offset_inventory_times_persist_as_utc_and_keep_paired_records_aligned(app, inventory):
    with app.app_context():
        mob = db.session.get(Mob, inventory["mob"])
        local_offset = timezone(timedelta(hours=2))
        created_at = datetime(2026, 1, 2, 0, 30, tzinfo=local_offset)
        moved_at = datetime(2026, 1, 3, 0, 30, tzinfo=local_offset)
        archived_at = datetime(2026, 1, 4, 0, 30, tzinfo=local_offset)
        MobInventoryService.ensure_baseline(mob, effective_at=created_at, source="created")
        _stock(inventory, 10, when=created_at)
        session = MovementService.move_mob(
            mob, [{"paddock_id": inventory["paddock"], "allocation_fraction": 1}],
            destination_farm_id=inventory["second"], when=moved_at,
        )
        MobService.archive_mob(mob, when=archived_at)
        session_id = session.id
        db.session.commit()
        db.session.expire_all()

        expected = [datetime(2026, 1, day, 22, 30) for day in (1, 2, 3)]
        states = MobInventoryStateHistory.query.filter_by(mob_id=mob.id).order_by(MobInventoryStateHistory.id).all()
        assert [row.effective_at for row in states] == expected
        assert StockLedgerEntry.query.filter_by(mob_id=mob.id).one().event_time == expected[0]
        assert MovementEvent.query.one().event_time == expected[1]
        saved_session = db.session.get(GrazingSession, session_id)
        assert (saved_session.start_at, saved_session.end_at) == (expected[1], expected[2])


def test_split_and_merge_capture_empty_creation_before_stock(app, inventory):
    with app.app_context():
        source = db.session.get(Mob, inventory["mob"])
        _stock(inventory, 10)
        when = datetime.now(timezone.utc) - timedelta(days=2)
        created = MovementService.split_mob(source, [{
            "name": "Split Inventory", "groups": [{
                "animal_group_type_id": inventory["group"], "quantity": 4,
            }],
        }], when=when)
        child = created[0]
        baseline = MobInventoryStateHistory.query.filter_by(mob_id=child.id).one()
        assert baseline.source == "created"
        assert baseline.effective_at.replace(tzinfo=timezone.utc) == when
        assert baseline.baseline_groups == []
        merged = MovementService.merge_mobs([source, child], "Merged Inventory", when=when)
        initial = MobInventoryStateHistory.query.filter_by(mob_id=merged.id).one()
        assert initial.source == "created" and initial.baseline_groups == []
        assert StockService.group_head_count(merged.id, inventory["group"]) == 10
        assert source.status == child.status == "archived"
        for old_mob in (source, child):
            assert MobInventoryStateHistory.query.filter_by(mob_id=old_mob.id, source="status").one().active is False


def test_api_creates_initial_state_and_records_arbitrary_active_transition(client, app, inventory):
    response = client.post("/api/mobs", json={"farm_id": inventory["first"], "name": "API Inventory"})
    assert response.status_code == 201
    mob_id = response.get_json()["id"]
    assert client.patch(f"/api/mobs/{mob_id}", json={"status": "inactive"}).status_code == 200
    with app.app_context():
        rows = MobInventoryStateHistory.query.filter_by(mob_id=mob_id).order_by(MobInventoryStateHistory.id).all()
        assert [row.source for row in rows] == ["created", "status"]
        assert [(row.previous_active, row.active) for row in rows] == [(None, True), (True, False)]


def test_invalid_api_creation_and_duplicate_rename_leave_no_extra_history(client, app, inventory):
    created = client.post("/api/mobs", json={"farm_id": inventory["first"], "name": "Other Inventory"})
    assert created.status_code == 201
    duplicate = client.post("/api/mobs", json={"farm_id": inventory["first"], "name": "Other Inventory"})
    assert duplicate.status_code == 400
    duplicate_rename = client.patch(f"/api/mobs/{inventory['mob']}", json={"name": "Other Inventory"})
    assert duplicate_rename.status_code == 400
    with app.app_context():
        assert MobInventoryStateHistory.query.count() == 1
        assert db.session.get(Mob, inventory["mob"]).name == "Inventory Mob"
