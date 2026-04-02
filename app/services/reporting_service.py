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
        paddock_count = sum(len([p for p in f.paddocks if p.status == "active"]) for f in Farm.query.all())
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
            .join(Mob)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
            )
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
    def paddock_current_stock_summary(paddock_id: str) -> list[dict]:
        rows = (
            GrazingAllocation.query.join(GrazingSession)
            .join(Mob)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
            )
            .all()
        )

        totals: dict[tuple[str, str, str, str], float] = {}
        for allocation in rows:
            fraction = float(allocation.allocation_fraction)
            for balance in allocation.grazing_session.mob.balances:
                group = balance.animal_group_type
                key = (
                    group.species,
                    group.breed,
                    group.sex,
                    group.age_class,
                )
                totals[key] = totals.get(key, 0.0) + (float(balance.head_count) * fraction)

        return [
            {
                "species": key[0],
                "breed": key[1],
                "sex": key[2],
                "age_class": key[3],
                "head_count": head_count,
            }
            for key, head_count in sorted(totals.items(), key=lambda item: item[0])
        ]

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
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _effective_session_end(
        cls,
        session: GrazingSession,
        *,
        default_open_end: datetime | None = None,
    ) -> datetime | None:
        if session.end_at is not None:
            end_at = session.end_at
        elif getattr(session.mob, "status", None) != "active":
            end_at = getattr(session.mob, "updated_at", None) or session.start_at
        else:
            end_at = default_open_end

        end_dt = cls._normalize_datetime(end_at) if end_at is not None else None
        start_dt = cls._normalize_datetime(session.start_at)
        if end_dt is not None and start_dt is not None and end_dt < start_dt:
            return start_dt
        return end_dt

    @classmethod
    def paddock_current_lsu_breakdown(cls, paddock_id: str) -> dict:
        rows = (
            GrazingAllocation.query.join(GrazingSession).join(Mob)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
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
            session_end = cls._effective_session_end(session, default_open_end=end_dt) or end_dt

            overlap_start = max(session_start, start_dt)
            overlap_end = min(session_end, end_dt)
            if overlap_end <= overlap_start:
                continue

            duration_days = (overlap_end - overlap_start).total_seconds() / 86400.0
            lsu_days += cls.allocation_lsu(allocation) * duration_days

        return lsu_days

    @classmethod
    def paddock_continuous_activity(cls, paddock, *, as_of: datetime | None = None) -> dict:
        now_dt = cls._normalize_datetime(as_of or datetime.utcnow())
        rows = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingAllocation.paddock_id == paddock.id,
                GrazingSession.start_at <= now_dt,
            )
            .order_by(GrazingSession.start_at.asc(), GrazingSession.end_at.asc())
            .all()
        )

        merged_intervals: list[list[datetime]] = []
        for allocation in rows:
            if cls.allocation_lsu(allocation) <= 0.0:
                continue
            session = allocation.grazing_session
            interval_start = cls._normalize_datetime(session.start_at)
            interval_end = cls._effective_session_end(session, default_open_end=now_dt) or now_dt
            if interval_end > now_dt:
                interval_end = now_dt
            if interval_end <= interval_start:
                continue

            if not merged_intervals or interval_start > merged_intervals[-1][1]:
                merged_intervals.append([interval_start, interval_end])
            else:
                merged_intervals[-1][1] = max(merged_intervals[-1][1], interval_end)

        if merged_intervals and merged_intervals[-1][1] >= now_dt:
            current_activity_state = "grazed"
            streak_start = merged_intervals[-1][0]
        else:
            current_activity_state = "rested"
            if merged_intervals:
                streak_start = merged_intervals[-1][1]
            else:
                created_at = getattr(paddock, "created_at", None)
                streak_start = cls._normalize_datetime(created_at) if created_at else now_dt
                if streak_start > now_dt:
                    streak_start = now_dt

        current_activity_days = max(0.0, (now_dt - streak_start).total_seconds() / 86400.0)
        return {
            "current_activity_state": current_activity_state,
            "current_activity_label": (
                "Days Grazed Continuously"
                if current_activity_state == "grazed"
                else "Days Rested Continuously"
            ),
            "current_activity_days": current_activity_days,
            "days_grazed_continuously": (
                current_activity_days if current_activity_state == "grazed" else None
            ),
            "days_rested_continuously": (
                current_activity_days if current_activity_state == "rested" else None
            ),
        }

    @staticmethod
    def mob_timeline(mob_id: str, limit: int = 50):
        return (
            MovementEvent.query.join(MovementEvent.mobs)
            .filter_by(mob_id=mob_id)
            .order_by(MovementEvent.event_time.desc())
            .limit(limit)
            .all()
        )
