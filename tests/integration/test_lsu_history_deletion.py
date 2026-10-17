from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance, AnimalGroupType, Farm, FarmCapacityHistory, Mob,
    MobInventoryBaselineGroup, MobInventoryStateHistory, Paddock,
)
from app.services.farm_capacity_service import FarmCapacityService
from app.services.farm_deletion_service import FarmDeletionService
from app.services.mob_inventory_service import MobInventoryService


def test_farm_deletion_removes_owned_history_and_preserves_surviving_mob_baseline(app):
    old_farm = Farm(name="Removed farm")
    kept_farm = Farm(name="Surviving farm")
    db.session.add_all([old_farm, kept_farm])
    db.session.flush()
    group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
    owned = Mob(farm_id=old_farm.id, name="Owned")
    moved = Mob(farm_id=old_farm.id, name="Moved")
    db.session.add_all([group, owned, moved, Paddock(farm_id=old_farm.id, name="Removed camp", area_ha=60)])
    db.session.flush()
    for mob in (owned, moved):
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=3))
        MobInventoryService.ensure_baseline(mob)
    FarmCapacityService.record(old_farm, source="baseline")
    FarmCapacityService.record(kept_farm, source="baseline")
    old_id, moved_id, owned_id, kept_id = old_farm.id, moved.id, owned.id, kept_farm.id
    moved.farm_id = kept_id
    MobInventoryService.record_state_change(
        moved, previous_farm_id=old_id, previous_active=True,
        effective_at=datetime.now(timezone.utc), source="move",
    )
    db.session.commit()
    FarmDeletionService.delete_farm_records(old_farm)
    db.session.commit()
    db.session.expire_all()

    assert FarmCapacityHistory.query.filter_by(farm_id=old_id).count() == 0
    assert FarmCapacityHistory.query.filter_by(farm_id=kept_id).count() == 1
    assert MobInventoryStateHistory.query.filter_by(mob_id=owned_id).count() == 0
    states = MobInventoryStateHistory.query.filter_by(mob_id=moved_id).order_by(MobInventoryStateHistory.id).all()
    assert len(states) == 2
    assert states[0].farm_id is None  # The lost ownership is explicit, not relabelled.
    assert states[1].previous_farm_id is None
    assert states[1].farm_id == kept_id
    assert MobInventoryBaselineGroup.query.count() == 1
    assert db.session.get(Mob, moved_id).balances[0].head_count == 3
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
