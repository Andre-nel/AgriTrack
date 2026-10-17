"""Explicit inventory history recording; transaction ownership stays with callers."""

from collections import defaultdict
from datetime import datetime, timezone

from app.extensions import db
from app.models.animal_group import AnimalGroupBalance
from app.models.mob import Mob
from app.models.mob_inventory import MobInventoryBaselineGroup, MobInventoryStateHistory
from app.models.stock_ledger import STOCK_IN_EVENTS, StockEventType, StockLedgerEntry


class MobInventoryService:
    @staticmethod
    def normalize_timestamp(value: datetime | None = None) -> datetime:
        """Use UTC before persistence; SQLite drops offsets from DateTime values."""
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def ensure_baseline(
        cls, mob: Mob, *, effective_at: datetime | None = None, source: str = "baseline",
    ) -> MobInventoryStateHistory:
        """Capture before the first mutation, including ledger entries dated in the future.

        Existing mobs receive a baseline at capture time. Callers creating a mob
        pass source='created' and its effective creation time before adding stock.
        The snapshot is grouped by animal type, so cohort partitions cannot count
        the same inventory twice. Reads must never call this method.
        """
        if source not in {"baseline", "created"}:
            raise ValueError("An inventory anchor must be a baseline or creation")
        db.session.flush()
        initial = (
            MobInventoryStateHistory.query.filter_by(mob_id=mob.id)
            .filter(MobInventoryStateHistory.source.in_(("baseline", "created")))
            .order_by(MobInventoryStateHistory.id).first()
        )
        if initial is not None:
            return initial

        counts = defaultdict(int)
        ledger_net = defaultdict(int)
        for row in AnimalGroupBalance.query.filter_by(mob_id=mob.id).all():
            counts[str(row.animal_group_type_id)] += int(row.head_count)
        for row in StockLedgerEntry.query.filter_by(mob_id=mob.id).all():
            # Historical count records have no signed interpretation. Preserve
            # the anchor, but the history reader must flag their period unknown.
            sign = 1 if row.event_type in STOCK_IN_EVENTS else -1
            if row.event_type != StockEventType.count:
                ledger_net[str(row.animal_group_type_id)] += sign * int(row.quantity)

        recorded_at = datetime.now(timezone.utc)
        initial = MobInventoryStateHistory(
            mob_id=mob.id, farm_id=mob.farm_id, active=mob.status == "active",
            previous_farm_id=None, previous_active=None,
            effective_at=cls.normalize_timestamp(effective_at or recorded_at),
            recorded_at=recorded_at, source=source,
        )
        db.session.add(initial)
        for group_id in sorted(counts.keys() | ledger_net.keys()):
            initial.baseline_groups.append(MobInventoryBaselineGroup(
                animal_group_type_id=group_id,
                head_count=counts[group_id], ledger_net=ledger_net[group_id],
            ))
        db.session.flush()
        return initial

    @staticmethod
    def record_state_change(
        mob: Mob, *, previous_farm_id: str | None, previous_active: bool,
        effective_at: datetime | None = None, source: str,
    ) -> MobInventoryStateHistory | None:
        """Record the before/after state after a caller has changed a captured mob."""
        if source not in {"move", "status"}:
            raise ValueError("An inventory state change must be a move or status change")
        active = mob.status == "active"
        if previous_farm_id == mob.farm_id and previous_active == active:
            return None
        if not MobInventoryStateHistory.query.filter_by(mob_id=mob.id).filter(
            MobInventoryStateHistory.source.in_(("baseline", "created"))
        ).first():
            raise ValueError("Capture the mob inventory baseline before changing its state")
        recorded_at = datetime.now(timezone.utc)
        row = MobInventoryStateHistory(
            mob_id=mob.id, farm_id=mob.farm_id, previous_farm_id=previous_farm_id,
            active=active, previous_active=previous_active,
            effective_at=MobInventoryService.normalize_timestamp(effective_at or recorded_at),
            recorded_at=recorded_at, source=source,
        )
        db.session.add(row)
        db.session.flush()
        return row
