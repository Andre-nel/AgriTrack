from datetime import datetime

from app.models import Mob
from app.services.grazing_service import GrazingService
from app.services.mob_inventory_service import MobInventoryService


class MobService:
    @staticmethod
    def set_status(mob: Mob, status: str, *, when: datetime | None = None) -> Mob:
        if (mob.status == "active") == (status == "active"):
            mob.status = status
            return mob
        MobInventoryService.ensure_baseline(mob)
        effective_at = MobInventoryService.normalize_timestamp(when)
        previous_farm_id, previous_active = mob.farm_id, mob.status == "active"
        mob.status = status
        MobInventoryService.record_state_change(
            mob, previous_farm_id=previous_farm_id, previous_active=previous_active,
            effective_at=effective_at, source="status",
        )
        return mob

    @staticmethod
    def archive_mob(mob: Mob, *, when: datetime | None = None) -> Mob:
        MobInventoryService.ensure_baseline(mob)
        archived_at = MobInventoryService.normalize_timestamp(when)
        GrazingService.close_open_session(mob_id=mob.id, end_at=archived_at)
        return MobService.set_status(mob, "archived", when=archived_at)
