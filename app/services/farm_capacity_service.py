from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db
from app.models.farm import Farm
from app.models.farm_capacity import FarmCapacityHistory
from app.models.paddock import Paddock


class FarmCapacityService:
    """Calculate capacity and explicitly record it within a caller's transaction."""

    QUANTUM = Decimal("0.000001")

    @staticmethod
    def calculate(farm: Farm, paddocks=None) -> Decimal:
        """Sum active paddock capacity using effective area and each paddock's rate.

        Passing paddocks makes this a pure calculation over the supplied objects.
        The default query reads database-scale values, including after a flush.
        """
        if paddocks is None:
            paddocks = db.session.query(
                Paddock.status,
                Paddock.area_ha,
                Paddock.grazeable_area_ha,
                Paddock.stocking_rate_ha_per_lsu_override,
            ).filter(Paddock.farm_id == farm.id).all()
            farm_rate = db.session.query(Farm.default_stocking_rate_ha_per_lsu).filter(
                Farm.id == farm.id
            ).scalar()
        else:
            farm_rate = farm.default_stocking_rate_ha_per_lsu

        total = Decimal("0")
        for paddock in paddocks:
            if paddock.status != "active":
                continue
            grazeable_area = Decimal(str(paddock.grazeable_area_ha or 0))
            area = grazeable_area if grazeable_area > 0 else Decimal(str(paddock.area_ha or 0))
            rate = paddock.stocking_rate_ha_per_lsu_override
            effective_rate = Decimal(str(rate if rate is not None else farm_rate))
            total += area / effective_rate
        return total

    @classmethod
    def record(
        cls,
        farm_or_id: Farm | str,
        *,
        effective_at: datetime | None = None,
        source: str = "change",
    ) -> FarmCapacityHistory | None:
        """Record the completed operation's total, without committing or backfilling.

        Call once after all capacity inputs have changed. Flushing before the
        calculation includes new/retired paddocks and database numeric precision.
        A rollback by the caller removes both its edits and this snapshot.
        """
        if source not in {"baseline", "created", "change"}:
            raise ValueError("Unknown farm capacity history source")
        db.session.flush()
        farm = farm_or_id if isinstance(farm_or_id, Farm) else db.session.get(Farm, farm_or_id)
        if farm is None:
            raise ValueError("Farm not found")
        total = cls.calculate(farm).quantize(cls.QUANTUM, rounding=ROUND_HALF_UP)
        previous = (
            FarmCapacityHistory.query.filter_by(farm_id=farm.id)
            .order_by(FarmCapacityHistory.effective_at.desc(), FarmCapacityHistory.id.desc())
            .first()
        )
        if previous is not None and previous.capacity_lsu == total:
            return None

        recorded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        effective_at = effective_at or recorded_at
        if effective_at.tzinfo is not None:
            effective_at = effective_at.astimezone(timezone.utc).replace(tzinfo=None)
        snapshot = FarmCapacityHistory(
            farm_id=farm.id,
            effective_at=effective_at,
            recorded_at=recorded_at,
            capacity_lsu=total,
            source=source,
        )
        db.session.add(snapshot)
        db.session.flush()
        return snapshot
