from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    JournalEntry,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
    PaddockEvent,
    RainfallRecord,
    StockLedgerEntry,
    WaterAsset,
    WaterAssetEvent,
)
from app.models.stock_ledger import StockEventType
from app.modules.analytics.constants import (
    ANALYTICS_GROUP_LABELS,
    ANALYTICS_STOCK_IN_TYPES,
    ANALYTICS_STOCK_OUT_TYPES,
)
from app.modules.analytics.forms import normalize_journal_tag, normalize_journal_tags
from app.services.mob_event_service import MobEventService


def _stock_delta_for_event(event_type: StockEventType, quantity: int) -> int:
    if event_type in ANALYTICS_STOCK_IN_TYPES:
        return quantity
    if event_type in ANALYTICS_STOCK_OUT_TYPES:
        return -quantity
    return 0


def _stock_group_dimensions(farm_name: str, group_type: AnimalGroupType) -> dict[str, str]:
    return {
        "species": (group_type.species or "Unknown").strip(),
        "breed": (group_type.breed or "Unknown").strip(),
        "sex": (group_type.sex or "Unknown").strip(),
        "age_class": (group_type.age_class or "Unknown").strip(),
        "farm": (farm_name or "Unknown").strip(),
    }


def _stock_series_label(group_by_fields: list[str], group_key: tuple[str, ...]) -> str:
    if len(group_by_fields) == 1:
        return group_key[0]

    parts = []
    for field, value in zip(group_by_fields, group_key):
        parts.append(f"{ANALYTICS_GROUP_LABELS[field]}: {value}")
    return " | ".join(parts)


def current_stock_totals_by_group(
    selected_filters: dict[str, str],
    group_by_fields: list[str],
) -> dict[tuple[str, ...], int]:
    rows = (
        db.session.query(AnimalGroupBalance, Mob, AnimalGroupType, Farm.name.label("farm_name"))
        .join(Mob, AnimalGroupBalance.mob_id == Mob.id)
        .join(AnimalGroupType, AnimalGroupBalance.animal_group_type_id == AnimalGroupType.id)
        .join(Farm, Mob.farm_id == Farm.id)
        .filter(Mob.status == "active", AnimalGroupBalance.head_count > 0)
    )

    if selected_filters["farm_id"]:
        rows = rows.filter(Mob.farm_id == selected_filters["farm_id"])
    if selected_filters["species"]:
        rows = rows.filter(AnimalGroupType.species == selected_filters["species"])
    if selected_filters["breed"]:
        rows = rows.filter(AnimalGroupType.breed == selected_filters["breed"])
    if selected_filters["sex"]:
        rows = rows.filter(AnimalGroupType.sex == selected_filters["sex"])
    if selected_filters["age_class"]:
        rows = rows.filter(AnimalGroupType.age_class == selected_filters["age_class"])

    totals: dict[tuple[str, ...], int] = defaultdict(int)
    for balance, _mob, group_type, farm_name in rows.all():
        dimensions = _stock_group_dimensions(farm_name=farm_name, group_type=group_type)
        group_key = tuple(dimensions[field] for field in group_by_fields)
        totals[group_key] += int(balance.head_count)

    return totals


def build_stock_tracking_chart_data(
    ledger_rows: list[tuple[StockLedgerEntry, str, AnimalGroupType]],
    group_by_fields: list[str],
    start_date: date,
    end_date: date,
    reconcile_to_current_totals: dict[tuple[str, ...], int] | None = None,
) -> tuple[dict, list[dict]]:
    labels = []
    cursor = start_date
    while cursor <= end_date:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)

    baseline_totals: dict[tuple[str, ...], int] = defaultdict(int)
    daily_deltas: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))

    for entry, farm_name, group_type in ledger_rows:
        event_date = entry.event_time.date()
        dimensions = _stock_group_dimensions(farm_name=farm_name, group_type=group_type)
        group_key = tuple(dimensions[field] for field in group_by_fields)
        delta = _stock_delta_for_event(event_type=entry.event_type, quantity=int(entry.quantity))
        if delta == 0:
            continue

        if event_date < start_date:
            baseline_totals[group_key] += delta
            continue
        if event_date > end_date:
            continue

        daily_deltas[event_date.isoformat()][group_key] += delta

    all_keys = set(baseline_totals.keys())
    for values in daily_deltas.values():
        all_keys.update(values.keys())
    if reconcile_to_current_totals:
        all_keys.update(reconcile_to_current_totals.keys())

    sorted_keys = sorted(all_keys, key=lambda row: tuple(value.lower() for value in row))
    running_totals = {key: baseline_totals.get(key, 0) for key in sorted_keys}
    series_by_key = {key: [] for key in sorted_keys}

    for label in labels:
        day_changes = daily_deltas.get(label, {})
        for key, delta in day_changes.items():
            running_totals[key] = running_totals.get(key, 0) + delta

        for key in sorted_keys:
            series_by_key[key].append(running_totals.get(key, 0))

    if reconcile_to_current_totals:
        for key in sorted_keys:
            desired_latest = int(reconcile_to_current_totals.get(key, 0))
            if not series_by_key[key]:
                continue
            observed_latest = int(series_by_key[key][-1])
            offset = desired_latest - observed_latest
            if offset:
                series_by_key[key] = [value + offset for value in series_by_key[key]]

    datasets = []
    latest_totals = []
    for key in sorted_keys:
        values = series_by_key[key]
        if not values or not any(value != 0 for value in values):
            continue
        label = _stock_series_label(group_by_fields=group_by_fields, group_key=key)
        datasets.append({"label": label, "values": values})
        latest_totals.append({"label": label, "head_count": values[-1]})

    latest_totals.sort(key=lambda row: (-row["head_count"], row["label"].lower()))
    return {"labels": labels, "datasets": datasets}, latest_totals


def _normalize_journal_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def build_analytics_journal_entries(
    farm_id: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    entries = []
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)

    journal_query = (
        db.session.query(JournalEntry, Farm.name.label("farm_name"))
        .join(Farm, JournalEntry.farm_id == Farm.id)
        .filter(JournalEntry.event_at >= start_dt, JournalEntry.event_at < end_dt)
    )
    if farm_id:
        journal_query = journal_query.filter(JournalEntry.farm_id == farm_id)

    for entry, farm_name in journal_query.all():
        tags = MobEventService.tags_from_csv(entry.tags_csv)
        if not tags:
            tags = normalize_journal_tags(["journal", f"farm {farm_name}"])
        entries.append(
            {
                "id": f"journal:{entry.id}",
                "event_at": _normalize_journal_datetime(entry.event_at),
                "source_label": "Journal",
                "farm_name": farm_name,
                "mob_name": "",
                "tags": tags,
                "description": entry.description,
            }
        )

    mob_event_query = (
        db.session.query(MobEvent, Farm.name.label("farm_name"), Mob.name.label("mob_name"))
        .join(Farm, MobEvent.farm_id == Farm.id)
        .join(Mob, MobEvent.mob_id == Mob.id)
        .filter(MobEvent.event_at >= start_dt, MobEvent.event_at < end_dt)
    )
    if farm_id:
        mob_event_query = mob_event_query.filter(MobEvent.farm_id == farm_id)

    for event, farm_name, mob_name in mob_event_query.all():
        tags = MobEventService.tags_from_csv(event.tags_csv)
        if not tags:
            tags = normalize_journal_tags(
                [
                    "mob event",
                    f"farm {farm_name}",
                    f"mob {mob_name}",
                ]
            )
        entries.append(
            {
                "id": f"mob_event:{event.id}",
                "event_at": _normalize_journal_datetime(event.event_at),
                "source_label": "Mob Event",
                "farm_name": farm_name,
                "mob_name": mob_name,
                "tags": tags,
                "description": event.description,
            }
        )

    paddock_event_query = (
        db.session.query(
            PaddockEvent,
            Farm.name.label("farm_name"),
            Paddock.name.label("paddock_name"),
        )
        .join(Farm, PaddockEvent.farm_id == Farm.id)
        .join(Paddock, PaddockEvent.paddock_id == Paddock.id)
        .filter(PaddockEvent.event_at >= start_dt, PaddockEvent.event_at < end_dt)
    )
    if farm_id:
        paddock_event_query = paddock_event_query.filter(PaddockEvent.farm_id == farm_id)

    for event, farm_name, paddock_name in paddock_event_query.all():
        tags = MobEventService.tags_from_csv(event.tags_csv)
        if not tags:
            tags = normalize_journal_tags(
                [
                    "paddock note",
                    f"farm {farm_name}",
                    f"paddock {paddock_name}",
                ]
            )
        entries.append(
            {
                "id": f"paddock_event:{event.id}",
                "event_at": _normalize_journal_datetime(event.event_at),
                "source_label": "Paddock Note",
                "farm_name": farm_name,
                "mob_name": paddock_name,
                "tags": tags,
                "description": event.description,
            }
        )

    water_asset_event_query = (
        db.session.query(
            WaterAssetEvent,
            Farm.name.label("farm_name"),
            WaterAsset.name.label("water_asset_name"),
        )
        .join(Farm, WaterAssetEvent.farm_id == Farm.id)
        .join(WaterAsset, WaterAssetEvent.water_asset_id == WaterAsset.id)
        .filter(WaterAssetEvent.event_at >= start_dt, WaterAssetEvent.event_at < end_dt)
    )
    if farm_id:
        water_asset_event_query = water_asset_event_query.filter(WaterAssetEvent.farm_id == farm_id)

    for event, farm_name, water_asset_name in water_asset_event_query.all():
        tags = MobEventService.tags_from_csv(event.tags_csv)
        if not tags:
            tags = normalize_journal_tags(
                [
                    "water asset note",
                    f"farm {farm_name}",
                    f"water asset {water_asset_name}",
                ]
            )
        entries.append(
            {
                "id": f"water_asset_event:{event.id}",
                "event_at": _normalize_journal_datetime(event.event_at),
                "source_label": "Water Asset Note",
                "farm_name": farm_name,
                "mob_name": water_asset_name,
                "tags": tags,
                "description": event.description,
            }
        )

    ledger_query = (
        db.session.query(
            StockLedgerEntry,
            Farm.name.label("farm_name"),
            Mob.name.label("mob_name"),
            AnimalGroupType,
        )
        .join(Farm, StockLedgerEntry.farm_id == Farm.id)
        .join(Mob, StockLedgerEntry.mob_id == Mob.id)
        .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
        .filter(StockLedgerEntry.event_time >= start_dt, StockLedgerEntry.event_time < end_dt)
    )
    if farm_id:
        ledger_query = ledger_query.filter(StockLedgerEntry.farm_id == farm_id)

    for entry, farm_name, mob_name, group_type in ledger_query.all():
        group_label = (
            f"{group_type.species} | {group_type.breed} | {group_type.sex} | {group_type.age_class}"
        )
        event_name = entry.event_type.value.replace("_", " ")
        description = (
            f"Stock {event_name} ({int(entry.quantity)}) recorded for {group_label} in mob {mob_name}."
        )
        note_text = (entry.note or "").strip()
        if note_text:
            description = f"{description}\n{note_text}"

        tags = normalize_journal_tags(
            [
                "stock",
                event_name,
                group_type.species,
                group_type.breed,
                group_type.sex,
                group_type.age_class,
                f"farm {farm_name}",
                f"mob {mob_name}",
            ]
        )
        entries.append(
            {
                "id": f"stock_ledger:{entry.id}",
                "event_at": _normalize_journal_datetime(entry.event_time),
                "source_label": "Stock Ledger",
                "farm_name": farm_name,
                "mob_name": mob_name,
                "tags": tags,
                "description": description,
            }
        )

    movement_query = (
        db.session.query(MovementEvent, Farm.name.label("farm_name"))
        .join(Farm, MovementEvent.farm_id == Farm.id)
        .filter(MovementEvent.event_time >= start_dt, MovementEvent.event_time < end_dt)
    )
    if farm_id:
        movement_query = movement_query.filter(MovementEvent.farm_id == farm_id)

    movement_rows = movement_query.all()
    movement_ids = [str(event.id) for event, _farm_name in movement_rows]
    movement_mobs_by_event: dict[str, list[MovementEventMob]] = defaultdict(list)
    mob_name_by_id = {}
    if movement_ids:
        movement_mob_rows = (
            MovementEventMob.query.filter(MovementEventMob.movement_event_id.in_(movement_ids)).all()
        )
        mob_ids = set()
        for row in movement_mob_rows:
            movement_mobs_by_event[str(row.movement_event_id)].append(row)
            mob_ids.add(str(row.mob_id))

        if mob_ids:
            mob_name_rows = db.session.query(Mob.id, Mob.name).filter(Mob.id.in_(mob_ids)).all()
            mob_name_by_id = {str(mob_id): mob_name for mob_id, mob_name in mob_name_rows}

    for event, farm_name in movement_rows:
        movement_kind = event.event_kind.value.replace("_", " ")
        linked = movement_mobs_by_event.get(str(event.id), [])
        mob_names = []
        for row in linked:
            mob_names.append(mob_name_by_id.get(str(row.mob_id), str(row.mob_id)))

        role_segments = sorted(
            f"{row.role.value}: {mob_name_by_id.get(str(row.mob_id), str(row.mob_id))}"
            for row in linked
        )
        description = f"Movement {movement_kind} event."
        if role_segments:
            description = f"{description} {'; '.join(role_segments)}."

        note_text = (event.source_note or "").strip()
        if note_text:
            description = f"{description}\n{note_text}"

        tags = normalize_journal_tags(
            [
                "movement",
                movement_kind,
                f"farm {farm_name}",
                *[f"mob {name}" for name in mob_names],
            ]
        )

        entries.append(
            {
                "id": f"movement:{event.id}",
                "event_at": _normalize_journal_datetime(event.event_time),
                "source_label": "Movement",
                "farm_name": farm_name,
                "mob_name": ", ".join(mob_names),
                "tags": tags,
                "description": description,
            }
        )

    rainfall_query = (
        db.session.query(RainfallRecord, Farm.name.label("farm_name"))
        .join(Farm, RainfallRecord.farm_id == Farm.id)
        .filter(RainfallRecord.recorded_on >= start_date, RainfallRecord.recorded_on <= end_date)
    )
    if farm_id:
        rainfall_query = rainfall_query.filter(RainfallRecord.farm_id == farm_id)

    for rainfall, farm_name in rainfall_query.all():
        description = f"Rainfall recorded: {float(rainfall.mm):.2f} mm (source: {rainfall.source})."
        note_text = (rainfall.note or "").strip()
        if note_text:
            description = f"{description}\n{note_text}"

        tags = normalize_journal_tags(
            [
                "rainfall",
                rainfall.source,
                f"farm {farm_name}",
            ]
        )

        entries.append(
            {
                "id": f"rainfall:{rainfall.id}",
                "event_at": datetime.combine(rainfall.recorded_on, time.min),
                "source_label": "Rainfall",
                "farm_name": farm_name,
                "mob_name": "",
                "tags": tags,
                "description": description,
            }
        )

    return entries


def group_analytics_journal_entries(
    entries: list[dict],
    selected_tag: str,
) -> list[dict]:
    tag_filter = normalize_journal_tag(selected_tag)
    if tag_filter:
        entries = [
            row
            for row in entries
            if any(tag_filter in tag for tag in row.get("tags", []))
        ]

    entries.sort(
        key=lambda row: (
            row["event_at"],
            row["source_label"].lower(),
            row["id"],
        ),
        reverse=True,
    )

    grouped = []
    for row in entries:
        day = row["event_at"].date()
        row["event_time"] = row["event_at"].strftime("%H:%M")
        if not grouped or grouped[-1]["date"] != day:
            grouped.append({"date": day, "entries": []})
        grouped[-1]["entries"].append(row)

    return grouped
