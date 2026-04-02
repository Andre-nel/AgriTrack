from datetime import datetime, timezone

from app.models import Mob
from app.services.grazing_service import GrazingService


class MobService:
    @staticmethod
    def archive_mob(mob: Mob, *, when: datetime | None = None) -> Mob:
        archived_at = when or datetime.now(timezone.utc)
        GrazingService.close_open_session(mob_id=mob.id, end_at=archived_at)
        mob.status = "archived"
        return mob
