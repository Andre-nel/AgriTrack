from datetime import datetime, timezone
from typing import Mapping

from app.extensions import db
from app.models import Mob
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.mob_event_service import MobEventService
from app.services.stock_service import StockService
from app.services.validation_service import ValidationService


STOCK_IN_EVENTS = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}


def _animal_group_label(group_type) -> str:
    return (
        f"{group_type.species} | {group_type.breed} | "
        f"{group_type.sex} | {group_type.age_class}"
    )


def _stock_event_label(event_type: StockEventType) -> str:
    return event_type.value.replace("_", " ")


def _join_tags(values: list[str]) -> str:
    tags = []
    seen = set()
    for value in values:
        tag = " ".join((value or "").strip().lower().split())
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return ",".join(tags)


def _stock_adjustment_tags(
    selected_event_type: StockEventType,
    posted_event_type: StockEventType,
    *extra_tags: str,
) -> str:
    values = ["stock", *extra_tags]
    if selected_event_type == StockEventType.count:
        values.append("count")
    values.extend([_stock_event_label(posted_event_type), "stock adjustment"])
    return _join_tags(values)


def _description_with_note(description: str, note: str | None) -> str:
    note_text = " ".join((note or "").strip().split())
    if not note_text:
        return description

    suffix = f" Note: {note_text}"
    max_length = MobEventService.MAX_DESCRIPTION_LENGTH
    if len(description) + len(suffix) <= max_length:
        return f"{description}{suffix}"

    available_note_length = max_length - len(description) - len(" Note: ")
    if available_note_length <= 0:
        return description[:max_length]
    return f"{description} Note: {note_text[:available_note_length].rstrip()}"


def _stock_adjustment_description(
    *,
    selected_event_type: StockEventType,
    posted_event_type: StockEventType,
    group_label: str,
    posted_quantity: int,
    previous_head_count: int,
    final_head_count: int,
    note: str | None,
) -> str:
    posted_label = _stock_event_label(posted_event_type)
    if selected_event_type == StockEventType.count:
        description = (
            f"Stock count recorded for {group_label}: "
            f"{previous_head_count} -> {final_head_count} head. "
            f"Posted {posted_label} of {posted_quantity}."
        )
    else:
        direction = "+" if posted_event_type in STOCK_IN_EVENTS else "-"
        description = (
            f"Stock {posted_label} recorded for {group_label}: "
            f"{direction}{posted_quantity} head. "
            f"Balance {previous_head_count} -> {final_head_count} head."
        )

    return _description_with_note(description, note)


def adjust_mob_stock_from_form(mob: Mob, form: Mapping[str, str]) -> str:
    try:
        group_type = StockService.get_or_create_group_type(
            species=(form.get("species") or "Cattle").strip(),
            breed=(form.get("breed") or "mixed").strip(),
            sex=(form.get("sex") or "mixed").strip(),
            age_class=(form.get("age_class") or "adult").strip(),
        )
        selected_event_type = StockEventType(form.get("event_type") or "adjustment_in")
        quantity = ValidationService.parse_whole_number(form.get("quantity"))

        event_type = selected_event_type
        event_quantity = quantity
        success_message = "Stock updated"
        selected_cohort_id = (form.get("cohort_id") or "").strip() or None
        if selected_cohort_id:
            current_balance = CohortService.balance_for_selector(
                mob_id=str(mob.id),
                animal_group_type_id=str(group_type.id),
                cohort_id=selected_cohort_id,
                require_positive=True,
            )
            if current_balance is None:
                raise ValueError("Selected cohort is no longer available in this mob")
            current_head_count = int(current_balance.head_count)
        else:
            current_head_count = StockService.group_head_count(str(mob.id), str(group_type.id))
        if selected_event_type == StockEventType.count:
            if quantity < 0:
                raise ValueError("Count must be greater than or equal to 0")
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

        final_head_count = (
            current_head_count + event_quantity
            if event_type in STOCK_IN_EVENTS
            else current_head_count - event_quantity
        )
        change_time = datetime.now(timezone.utc)
        posting_arguments = dict(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=group_type.id,
            event_type=event_type,
            quantity=event_quantity,
            note=form.get("note"),
            event_time=change_time,
            allocation_paddock_id=(
                (form.get("allocation_paddock_id") or form.get("adjust_paddock_id") or "").strip()
                or None
            ),
        )
        if selected_cohort_id:
            StockService.adjust_stock(cohort_id=selected_cohort_id, **posting_arguments)
        else:
            StockService.adjust_group_stock(**posting_arguments)
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=_stock_adjustment_description(
                selected_event_type=selected_event_type,
                posted_event_type=event_type,
                group_label=_animal_group_label(group_type),
                posted_quantity=event_quantity,
                previous_head_count=int(current_head_count),
                final_head_count=int(final_head_count),
                note=form.get("note"),
            ),
            raw_tags=_stock_adjustment_tags(selected_event_type, event_type),
            event_at=change_time,
        )
        db.session.commit()
        return success_message
    except ValueError:
        db.session.rollback()
        raise


def reclassify_mob_stock_from_form(mob: Mob, form: Mapping[str, str]) -> str:
    """Move a counted population between classifications in one transaction."""
    try:
        if mob.status != "active":
            raise ValueError("Selected mob must be active")
        source_group_type_id = (form.get("source_animal_group_type_id") or "").strip()
        if not source_group_type_id:
            raise ValueError("Select a balance line to reclassify")
        if "head_count" in form or (form.get("source_cohort_id") or "").strip():
            raise ValueError("Balance editing has changed. Refresh the mob page before reclassifying.")
        source_balances = StockService.group_balances(str(mob.id), source_group_type_id)
        if not source_balances:
            raise ValueError("Selected balance line is no longer available. Refresh and try again.")
        source_group = source_balances[0].animal_group_type
        source_head = sum(int(balance.head_count) for balance in source_balances)
        quantity = ValidationService.parse_whole_number(form.get("quantity"), "Number to reclassify")
        if quantity < 1 or quantity > source_head:
            raise ValueError(f"Number to reclassify must be between 1 and {source_head}")

        target_group = StockService.get_or_create_group_type(
            species=source_group.species,
            breed=source_group.breed,
            sex=(form.get("sex") or "").strip(),
            age_class=(form.get("age_class") or "").strip(),
        )
        if str(target_group.id) == str(source_group.id):
            raise ValueError("Choose a different sex or age class to reclassify animals")
        target_head = StockService.group_head_count(str(mob.id), str(target_group.id))
        source_label = _animal_group_label(source_group)
        target_label = _animal_group_label(target_group)
        description = (
            f"Reclassified {quantity} head from {source_label} to {target_label}. "
            f"Source balance {source_head} -> {source_head - quantity}; "
            f"destination balance {target_head} -> {target_head + quantity}. "
            "Total head count unchanged."
        )
        ledger_note = _description_with_note(description, form.get("note"))
        change_time = datetime.now(timezone.utc)
        StockService.reclassify_group_stock(
            mob,
            source_group_id=str(source_group.id),
            target_group=target_group,
            quantity=quantity,
            note=ledger_note,
            event_time=change_time,
        )
        if len(source_balances) > 1:
            description = StockService.proportional_note(description)
        description = _description_with_note(description, form.get("note"))
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=description,
            raw_tags="stock,reclassification,adjustment out,adjustment in,stock adjustment",
            event_at=change_time,
        )
        db.session.commit()
        return f"Reclassified {quantity} head. Total head count unchanged."
    except Exception:
        db.session.rollback()
        raise
