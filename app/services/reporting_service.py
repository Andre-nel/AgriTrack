from datetime import datetime, timezone

from sqlalchemy import or_

from app.models import Farm, Mob, MovementEvent
from app.models.grazing import GrazingAllocation, GrazingSession


class ReportingService:
    FEMALE_BASE_LSU = {
        "cattle": 1.0,
        "sheep": 1.0 / 6.0,
        "goat": 1.0 / 8.0,
    }

    @staticmethod
    def dashboard_summary() -> dict:
        farm_count = Farm.query.count()
        paddock_count = sum(len(f.paddocks) for f in Farm.query.all())
        mob_count = Mob.query.filter_by(status="active").count()
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
    def _normalize_text(value: str | None) -> str:
        return (value or "").strip().lower()

    @classmethod
    def group_lsu_per_head(cls, species: str, sex: str, age_class: str) -> float:
        species_key = cls._normalize_text(species)
        sex_key = cls._normalize_text(sex)
        age_key = cls._normalize_text(age_class)

        base = cls.FEMALE_BASE_LSU.get(species_key, 1.0)

        juvenile_labels = {
            "cattle": {"calf"},
            "sheep": {"lamb"},
            "goat": {"kid"},
        }
        if age_key in juvenile_labels.get(species_key, set()):
            age_multiplier = 0.3
        elif age_key == "young":
            age_multiplier = 0.8
        else:
            age_multiplier = 1.0

        is_male = sex_key in {"ram", "bul", "bull"}
        if is_male and age_key in {"adult", "old"}:
            sex_multiplier = 1.75
        elif is_male and age_key == "young":
            sex_multiplier = 1.2
        else:
            sex_multiplier = 1.0

        return base * age_multiplier * sex_multiplier

    @classmethod
    def mob_total_lsu(cls, mob) -> float:
        total = 0.0
        for balance in mob.balances:
            group = balance.animal_group_type
            per_head_lsu = cls.group_lsu_per_head(group.species, group.sex, group.age_class)
            total += float(balance.head_count) * per_head_lsu
        return total

    @classmethod
    def allocation_lsu(cls, allocation: GrazingAllocation) -> float:
        mob_total = cls.mob_total_lsu(allocation.grazing_session.mob)
        return mob_total * float(allocation.allocation_fraction)

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def paddock_current_lsu_breakdown(cls, paddock_id: str) -> dict:
        rows = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
            )
            .all()
        )

        total_lsu = 0.0
        mobs = []
        for allocation in rows:
            allocated_lsu = cls.allocation_lsu(allocation)
            total_lsu += allocated_lsu
            mobs.append(
                {
                    "mob_id": str(allocation.grazing_session.mob_id),
                    "mob_name": allocation.grazing_session.mob.name,
                    "allocation_fraction": float(allocation.allocation_fraction),
                    "allocated_lsu": allocated_lsu,
                }
            )

        mobs.sort(key=lambda row: row["allocated_lsu"], reverse=True)
        return {"total_lsu": total_lsu, "mobs": mobs}

    @classmethod
    def paddock_lsu_days_for_period(
        cls,
        paddock_id: str,
        period_start: datetime,
        period_end: datetime,
    ) -> float:
        start_dt = cls._normalize_datetime(period_start)
        end_dt = cls._normalize_datetime(period_end)
        now_dt = cls._normalize_datetime(datetime.utcnow())
        if end_dt > now_dt:
            end_dt = now_dt
        if end_dt <= start_dt:
            return 0.0

        rows = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.start_at < end_dt,
                or_(GrazingSession.end_at.is_(None), GrazingSession.end_at > start_dt),
            )
            .all()
        )

        lsu_days = 0.0
        for allocation in rows:
            session = allocation.grazing_session
            session_start = cls._normalize_datetime(session.start_at)
            session_end = cls._normalize_datetime(session.end_at) if session.end_at else end_dt

            overlap_start = max(session_start, start_dt)
            overlap_end = min(session_end, end_dt)
            if overlap_end <= overlap_start:
                continue

            duration_days = (overlap_end - overlap_start).total_seconds() / 86400.0
            lsu_days += cls.allocation_lsu(allocation) * duration_days

        return lsu_days

    @staticmethod
    def mob_timeline(mob_id: str, limit: int = 50):
        return (
            MovementEvent.query.join(MovementEvent.mobs)
            .filter_by(mob_id=mob_id)
            .order_by(MovementEvent.event_time.desc())
            .limit(limit)
            .all()
        )
