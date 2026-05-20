from datetime import datetime, timezone
from typing import Mapping

from app.extensions import db
from app.models import AnimalGroupBalance, Mob
from app.models.stock_ledger import StockEventType
from app.services.grazing_history_service import GrazingHistoryService
from app.services.mob_event_service import MobEventService
from app.services.stock_service import StockService


def adjust_mob_stock_from_form(mob: Mob, form: Mapping[str, str]) -> str:
    try:
        group_type = StockService.get_or_create_group_type(
            species=(form.get("species") or "Cattle").strip(),
            breed=(form.get("breed") or "mixed").strip(),
            sex=(form.get("sex") or "mixed").strip(),
            age_class=(form.get("age_class") or "adult").strip(),
        )
        selected_event_type = StockEventType(form.get("event_type") or "adjustment_in")
        quantity = int(form.get("quantity") or 0)

        event_type = selected_event_type
        event_quantity = quantity
        success_message = "Stock updated"
        if selected_event_type == StockEventType.count:
            current_balance = (
                AnimalGroupBalance.query.filter_by(
                    mob_id=mob.id,
                    animal_group_type_id=group_type.id,
                ).first()
            )
            current_head_count = current_balance.head_count if current_balance else 0
            delta = quantity - current_head_count
            if delta == 0:
                return "Count matches current balance. No stock adjustment posted."
            if delta > 0:
                event_type = StockEventType.adjustment_in
                event_quantity = delta
            else:
                event_type = StockEventType.missing
                event_quantity = -delta
            success_message = (
                f"Count set to {quantity}. Posted {event_type.value} of {event_quantity}."
            )

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=group_type.id,
            event_type=event_type,
            quantity=event_quantity,
            note=form.get("note"),
        )
        db.session.commit()
        return success_message
    except ValueError:
        db.session.rollback()
        raise


def update_mob_balance_line_from_form(mob: Mob, form: Mapping[str, str]) -> str:
    source_group_type_id = (form.get("source_animal_group_type_id") or "").strip()
    if not source_group_type_id:
        raise ValueError("Select a balance line to edit")

    source_balance = AnimalGroupBalance.query.filter_by(
        mob_id=mob.id,
        animal_group_type_id=source_group_type_id,
    ).first()
    if not source_balance or int(source_balance.head_count) <= 0:
        raise ValueError("Selected balance line is no longer available. Refresh and try again.")

    source_group = source_balance.animal_group_type
    source_head = int(source_balance.head_count)
    head_count_raw = (form.get("head_count") or "").strip()
    try:
        target_head = int(head_count_raw)
    except ValueError:
        raise ValueError("Head count must be a whole number")

    if target_head <= 0:
        raise ValueError("Head count must be greater than 0")

    note_text = " ".join((form.get("note") or "").strip().split())

    try:
        target_group = StockService.get_or_create_group_type(
            species=source_group.species,
            breed=source_group.breed,
            sex=(form.get("sex") or source_group.sex).strip(),
            age_class=(form.get("age_class") or source_group.age_class).strip(),
        )

        source_label = (
            f"{source_group.species} | {source_group.breed} | "
            f"{source_group.sex} | {source_group.age_class}"
        )
        target_label = (
            f"{target_group.species} | {target_group.breed} | "
            f"{target_group.sex} | {target_group.age_class}"
        )
        unchanged = str(target_group.id) == str(source_group.id) and target_head == source_head
        if unchanged:
            return "No changes detected for the selected balance line"

        if str(target_group.id) == str(source_group.id):
            delta = target_head - source_head
            event_type = StockEventType.adjustment_in if delta > 0 else StockEventType.adjustment_out
            quantity = abs(delta)
            description = (
                f"Balance head updated for {source_label}: {source_head} -> {target_head}."
            )
            event_tags = "stock,balance edit,head adjustment"
            change_time = datetime.now(timezone.utc)
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=source_group.id,
                event_type=event_type,
                quantity=quantity,
                note=f"{description} Note: {note_text}" if note_text else description,
                event_time=change_time,
            )
        else:
            description = (
                f"Balance reclassified from {source_label} (head: {source_head}) "
                f"to {target_label} (head: {target_head})."
            )
            event_tags = "stock,balance edit,reclassification"
            ledger_note = f"{description} Note: {note_text}" if note_text else description
            change_time = datetime.now(timezone.utc)
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=source_group.id,
                event_type=StockEventType.adjustment_out,
                quantity=source_head,
                note=ledger_note,
                event_time=change_time,
                sync_grazing_history=False,
            )
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=target_group.id,
                event_type=StockEventType.adjustment_in,
                quantity=target_head,
                note=ledger_note,
                event_time=change_time,
                sync_grazing_history=False,
            )
            GrazingHistoryService.sync_live_history_for_mob(mob, effective_at=change_time)

        event_description = f"{description} Note: {note_text}" if note_text else description
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=event_description,
            raw_tags=event_tags,
            event_at=change_time,
        )
        db.session.commit()
        return "Balance line updated"
    except ValueError:
        db.session.rollback()
        raise
