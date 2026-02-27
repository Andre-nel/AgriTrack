from sqlalchemy import func

from app.models import Farm, MovementEvent
from app.models.grazing import GrazingAllocation, GrazingSession


class ReportingService:
    @staticmethod
    def dashboard_summary() -> dict:
        farm_count = Farm.query.count()
        paddock_count = sum(len(f.paddocks) for f in Farm.query.all())
        mob_count = sum(len(f.mobs) for f in Farm.query.all())
        return {
            "farm_count": farm_count,
            "paddock_count": paddock_count,
            "mob_count": mob_count,
        }

    @staticmethod
    def paddock_current_stock(paddock_id: str) -> dict:
        rows = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(GrazingAllocation.paddock_id == paddock_id, GrazingSession.end_at.is_(None))
            .all()
        )

        totals = {}
        for allocation in rows:
            fraction = float(allocation.allocation_fraction)
            for balance in allocation.grazing_session.mob.balances:
                key = balance.animal_group_type_id
                totals[key] = totals.get(key, 0.0) + (balance.head_count * fraction)
        return totals

    @staticmethod
    def mob_timeline(mob_id: str, limit: int = 50):
        return (
            MovementEvent.query.join(MovementEvent.mobs)
            .filter_by(mob_id=mob_id)
            .order_by(MovementEvent.event_time.desc())
            .limit(limit)
            .all()
        )
