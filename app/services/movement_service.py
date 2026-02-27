from datetime import datetime, timezone

from app.extensions import db
from app.models import Mob, MovementEvent, MovementEventMob
from app.models.animal_group import AnimalGroupBalance
from app.models.movement import MobLineage, MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType
from app.services.grazing_service import GrazingService
from app.services.stock_service import StockService


class MovementService:
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
