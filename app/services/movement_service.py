from datetime import datetime, timezone
from collections import defaultdict

from app.extensions import db
from app.models import Mob, MovementEvent, MovementEventMob
from app.models.animal_group import AnimalGroupBalance
from app.models.movement import MobLineage, MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType
from app.services.grazing_service import GrazingService
from app.services.stock_service import StockService


class MovementService:
    @staticmethod
    def _normalize_splits(source_mob: Mob, splits: list[dict]) -> list[dict]:
        if not splits:
            raise ValueError("At least two split mobs are required")

        source_balances = {
            str(balance.animal_group_type_id): int(balance.head_count)
            for balance in source_mob.balances
            if int(balance.head_count) > 0
        }
        if not source_balances:
            raise ValueError("Source mob has no stock to split")

        by_mob: dict[str, dict[str, int]] = defaultdict(dict)
        allocated_totals = {group_id: 0 for group_id in source_balances}

        for split in splits:
            name = (split.get("name") or "").strip()
            if not name:
                raise ValueError("Each split requires a mob name")
            if name.lower() == source_mob.name.lower():
                raise ValueError("Split mob name cannot match the source mob name")

            groups = split.get("groups", [])
            if not groups:
                raise ValueError(f"Split mob '{name}' must include at least one group allocation")

            mob_group_totals = by_mob[name]
            for group in groups:
                group_id = str(group.get("animal_group_type_id") or "").strip()
                if group_id not in source_balances:
                    raise ValueError("Split contains an invalid animal group for this source mob")

                try:
                    qty = int(group.get("quantity", 0))
                except (TypeError, ValueError):
                    raise ValueError("Split quantities must be whole numbers")
                if qty <= 0:
                    raise ValueError("Split quantities must be greater than 0")

                mob_group_totals[group_id] = mob_group_totals.get(group_id, 0) + qty
                allocated_totals[group_id] += qty

        normalized = []
        for name, groups in by_mob.items():
            normalized_groups = [
                {"animal_group_type_id": group_id, "quantity": qty}
                for group_id, qty in groups.items()
            ]
            normalized.append({"name": name, "groups": normalized_groups})

        if len(normalized) < 2:
            raise ValueError("Split must create at least two mobs")

        for group_id, source_qty in source_balances.items():
            allocated_qty = allocated_totals.get(group_id, 0)
            if allocated_qty != source_qty:
                raise ValueError(
                    "Split allocations must fully allocate each source group "
                    f"(group {group_id}: allocated {allocated_qty}, available {source_qty})"
                )

        return normalized

    @staticmethod
    def move_mob(mob: Mob, allocations: list[dict], when=None):
        when = when or datetime.now(timezone.utc)

        GrazingService.close_open_session(mob_id=mob.id, end_at=when)
        session = GrazingService.open_session(
            farm_id=mob.farm_id,
            mob_id=mob.id,
            start_at=when,
            allocations=allocations,
        )

        event = MovementEvent(
            farm_id=mob.farm_id,
            event_time=when,
            event_kind=MovementEventKind.move,
        )
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(movement_event_id=event.id, mob_id=mob.id, role=MovementRole.source)
        )

        return session

    @staticmethod
    def split_mob(source_mob: Mob, splits: list[dict], when=None):
        splits = MovementService._normalize_splits(source_mob=source_mob, splits=splits)
        when = when or datetime.now(timezone.utc)
        event = MovementEvent(farm_id=source_mob.farm_id, event_time=when, event_kind=MovementEventKind.split)
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(
                movement_event_id=event.id, mob_id=source_mob.id, role=MovementRole.source
            )
        )

        created = []
        for split in splits:
            new_mob = Mob(farm_id=source_mob.farm_id, name=split["name"], status="active")
            db.session.add(new_mob)
            db.session.flush()
            created.append(new_mob)
            db.session.add(
                MovementEventMob(
                    movement_event_id=event.id,
                    mob_id=new_mob.id,
                    role=MovementRole.result,
                )
            )
            db.session.add(
                MobLineage(
                    parent_mob_id=source_mob.id,
                    child_mob_id=new_mob.id,
                    movement_event_id=event.id,
                )
            )

            for group in split.get("groups", []):
                qty = int(group["quantity"])
                group_id = group["animal_group_type_id"]
                StockService.adjust_stock(
                    mob_id=source_mob.id,
                    farm_id=source_mob.farm_id,
                    animal_group_type_id=group_id,
                    event_type=StockEventType.transfer_out,
                    quantity=qty,
                    note="split out",
                )
                StockService.adjust_stock(
                    mob_id=new_mob.id,
                    farm_id=source_mob.farm_id,
                    animal_group_type_id=group_id,
                    event_type=StockEventType.transfer_in,
                    quantity=qty,
                    note="split in",
                )

        return created

    @staticmethod
    def merge_mobs(source_mobs: list[Mob], result_name: str, when=None):
        when = when or datetime.now(timezone.utc)
        if not source_mobs:
            raise ValueError("At least one source mob is required")

        farm_id = source_mobs[0].farm_id
        result_mob = Mob(farm_id=farm_id, name=result_name, status="active")
        db.session.add(result_mob)
        db.session.flush()

        event = MovementEvent(farm_id=farm_id, event_time=when, event_kind=MovementEventKind.merge)
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(
                movement_event_id=event.id, mob_id=result_mob.id, role=MovementRole.result
            )
        )

        for source in source_mobs:
            db.session.add(
                MovementEventMob(
                    movement_event_id=event.id, mob_id=source.id, role=MovementRole.source
                )
            )
            db.session.add(
                MobLineage(
                    parent_mob_id=source.id,
                    child_mob_id=result_mob.id,
                    movement_event_id=event.id,
                )
            )
            balances = AnimalGroupBalance.query.filter_by(mob_id=source.id).all()
            for balance in balances:
                if balance.head_count == 0:
                    continue
                StockService.adjust_stock(
                    mob_id=source.id,
                    farm_id=farm_id,
                    animal_group_type_id=balance.animal_group_type_id,
                    event_type=StockEventType.transfer_out,
                    quantity=balance.head_count,
                    note="merge out",
                )
                StockService.adjust_stock(
                    mob_id=result_mob.id,
                    farm_id=farm_id,
                    animal_group_type_id=balance.animal_group_type_id,
                    event_type=StockEventType.transfer_in,
                    quantity=balance.head_count,
                    note="merge in",
                )
            source.status = "archived"

        return result_mob
