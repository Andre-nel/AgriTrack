from decimal import Decimal

from app.models import Farm, Mob, MobEvent, Paddock
from app.modules.tasks.entity_links import linked_task_rows_for_entity
from app.models.stock_ledger import StockEventType
from app.services.mob_event_service import MobEventService


def build_mob_detail_context(mob: Mob, selected_event_tag: str) -> dict:
    farms = Farm.query.order_by(Farm.name).all()
    all_paddocks = Paddock.query.filter_by(status="active").order_by(Paddock.name).all()
    all_active_mobs = Mob.query.filter_by(status="active").order_by(Mob.name).all()
    move_farms = [{"id": str(farm.id), "name": farm.name} for farm in farms]
    move_paddocks_by_farm = {str(farm.id): [] for farm in farms}
    transfer_mobs_by_farm = {str(farm.id): [] for farm in farms}
    for paddock in all_paddocks:
        move_paddocks_by_farm.setdefault(str(paddock.farm_id), []).append(
            {"id": str(paddock.id), "name": paddock.name}
        )
    for item in all_active_mobs:
        if str(item.id) == str(mob.id):
            continue
        transfer_mobs_by_farm.setdefault(str(item.farm_id), []).append(
            {"id": str(item.id), "name": item.name}
        )

    active_session = next((session for session in mob.grazing_sessions if session.end_at is None), None)
    current_allocations = []
    allocation_total_pct = Decimal("0")
    if active_session:
        for allocation in sorted(active_session.allocations, key=lambda a: a.paddock.name.lower()):
            pct = Decimal(str(allocation.allocation_fraction)) * Decimal("100")
            allocation_total_pct += pct
            current_allocations.append(
                {
                    "paddock_id": str(allocation.paddock_id),
                    "paddock_name": allocation.paddock.name,
                    "allocation_pct": float(pct),
                }
            )

    split_group_options = []
    for balance in sorted(
        mob.balances,
        key=lambda b: (
            b.animal_group_type.species.lower(),
            b.animal_group_type.breed.lower(),
            b.animal_group_type.sex.lower(),
            b.animal_group_type.age_class.lower(),
        ),
    ):
        if int(balance.head_count) <= 0:
            continue
        group = balance.animal_group_type
        split_group_options.append(
            {
                "id": str(balance.animal_group_type_id),
                "species": group.species,
                "breed": group.breed,
                "sex": group.sex,
                "age_class": group.age_class,
                "head_count": int(balance.head_count),
                "label": (
                    f"{group.species} | {group.breed} | {group.sex} | {group.age_class} "
                    f"(available: {int(balance.head_count)})"
                ),
            }
        )

    mob_events_all = []
    event_rows = (
        MobEvent.query.filter_by(mob_id=mob.id)
        .order_by(MobEvent.event_at.desc(), MobEvent.created_at.desc())
        .all()
    )
    for event in event_rows:
        tags = MobEventService.tags_from_csv(event.tags_csv)
        mob_events_all.append(
            {
                "id": str(event.id),
                "event_at": event.event_at,
                "tags": tags,
                "description": event.description,
                "attachments": sorted(
                    event.attachments,
                    key=lambda attachment: attachment.created_at,
                    reverse=True,
                ),
            }
        )
    event_tag_options = sorted({tag for row in mob_events_all for tag in row["tags"]})
    mob_events = [
        row for row in mob_events_all if not selected_event_tag or selected_event_tag in row["tags"]
    ]

    return {
        "mob": mob,
        "move_farms": move_farms,
        "move_paddocks_by_farm": move_paddocks_by_farm,
        "has_move_paddocks": any(len(rows) > 0 for rows in move_paddocks_by_farm.values()),
        "default_move_farm_id": str(mob.farm_id),
        "transfer_mobs_by_farm": transfer_mobs_by_farm,
        "has_transfer_destination_mobs": any(
            len(rows) > 0 for rows in transfer_mobs_by_farm.values()
        ),
        "default_transfer_farm_id": str(mob.farm_id),
        "stock_event_types": StockEventType,
        "current_allocations": current_allocations,
        "allocation_total_pct": float(allocation_total_pct),
        "split_group_options": split_group_options,
        "mob_events": mob_events,
        "event_tag_options": event_tag_options,
        "selected_event_tag": selected_event_tag,
        "linked_task_rows": linked_task_rows_for_entity(
            mob_id=str(mob.id),
            tz_name=mob.farm.timezone if mob.farm else "UTC",
        ),
    }
