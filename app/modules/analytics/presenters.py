import re
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
    if not group_by_fields:
        return "Total"
    if len(group_by_fields) == 1:
        return group_key[0]

    parts = []
    for field, value in zip(group_by_fields, group_key):
        parts.append(f"{ANALYTICS_GROUP_LABELS[field]}: {value}")
    return " | ".join(parts)


def current_stock_totals_by_group(
    selected_filters: dict[str, str | list[str]],
    group_by_fields: list[str],
    mob_id: str | None = None,
) -> dict[tuple[str, ...], int]:
    rows = (
        db.session.query(AnimalGroupBalance, Mob, AnimalGroupType, Farm.name.label("farm_name"))
        .join(Mob, AnimalGroupBalance.mob_id == Mob.id)
        .join(AnimalGroupType, AnimalGroupBalance.animal_group_type_id == AnimalGroupType.id)
        .join(Farm, Mob.farm_id == Farm.id)
        .filter(Mob.status == "active", AnimalGroupBalance.head_count > 0)
    )
    if mob_id is not None:
        rows = rows.filter(Mob.id == mob_id)

    selected_farm_ids = selected_filters.get("farm_ids") or []
    if isinstance(selected_farm_ids, str):
        selected_farm_ids = [selected_farm_ids] if selected_farm_ids else []
    if not selected_farm_ids:
        selected_farm_id = selected_filters.get("farm_id") or ""
        if isinstance(selected_farm_id, str) and selected_farm_id:
            selected_farm_ids = [selected_farm_id]
    if selected_farm_ids:
        rows = rows.filter(Mob.farm_id.in_(selected_farm_ids))
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


_STOCK_NOTE_HIGHLIGHT_PATTERN = re.compile(r"\b(missing|kort|short|mis|weg)\b", re.IGNORECASE)
_STOCK_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _stock_event_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stock_timestamp_ms(value: datetime) -> float:
    elapsed = value - _STOCK_EPOCH
    return elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds / 1000


def _stock_chart_event(
    entry: StockLedgerEntry,
    event_at: datetime,
    farm_name: str,
    group_type: AnimalGroupType,
    delta: int,
) -> dict:
    note = entry.note or ""
    reasons = []
    if entry.event_type == StockEventType.missing:
        reasons.append("Missing stock recorded")
    keywords = dict.fromkeys(
        match.group(0).lower() for match in _STOCK_NOTE_HIGHLIGHT_PATTERN.finditer(note)
    )
    reasons.extend(f"Note keyword: {keyword}" for keyword in keywords)
    dimensions = _stock_group_dimensions(farm_name, group_type)
    return {
        "id": str(entry.id),
        "event_time": event_at.isoformat().replace("+00:00", "Z"),
        "event_type": entry.event_type.value,
        "event_label": entry.event_type.value.replace("_", " ").capitalize(),
        "delta": delta,
        "mob_id": str(entry.mob_id),
        "mob_name": entry.mob.name if entry.mob is not None else "Unknown",
        "farm_name": farm_name,
        "animal_type": " | ".join(
            dimensions[field] for field in ("species", "breed", "sex", "age_class")
        ),
        "note": note,
        "highlight_missing": bool(reasons),
        "highlight_reasons": reasons,
    }


def build_stock_tracking_chart_data(
    ledger_rows: list[tuple[StockLedgerEntry, str, AnimalGroupType]],
    group_by_fields: list[str],
    start_date: date,
    end_date: date,
    reconcile_to_current_totals: dict[tuple[str, ...], int] | None = None,
) -> tuple[dict, list[dict]]:
    """Build UTC stock steps, applying simultaneous ledger postings atomically per series."""
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    min_x = _stock_timestamp_ms(start_at)
    max_x = _stock_timestamp_ms(end_at)
    baseline_totals: dict[tuple[str, ...], int] = defaultdict(int)
    events_by_group: dict[tuple[str, ...], dict[datetime, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    ordered_rows = sorted(
        ledger_rows,
        key=lambda row: (_stock_event_datetime(row[0].event_time), str(row[0].id)),
    )
    for entry, farm_name, group_type in ordered_rows:
        event_at = _stock_event_datetime(entry.event_time)
        if event_at >= end_at:
            continue
        dimensions = _stock_group_dimensions(farm_name=farm_name, group_type=group_type)
        group_key = tuple(dimensions[field] for field in group_by_fields)
        delta = _stock_delta_for_event(event_type=entry.event_type, quantity=int(entry.quantity))
        if delta == 0:
            continue

        if event_at < start_at:
            baseline_totals[group_key] += delta
            continue
        events_by_group[group_key][event_at].append(
            _stock_chart_event(entry, event_at, farm_name, group_type, delta)
        )

    all_keys = set(baseline_totals) | set(events_by_group)
    if reconcile_to_current_totals is not None:
        all_keys.update(reconcile_to_current_totals.keys())
    sorted_keys = sorted(all_keys, key=lambda row: tuple(value.lower() for value in row))
    datasets = []
    latest_totals = []
    for key in sorted_keys:
        timestamp_events = events_by_group.get(key, {})
        opening_count = baseline_totals.get(key, 0)
        observed_latest = opening_count + sum(
            event["delta"] for events in timestamp_events.values() for event in events
        )
        opening_adjustment = 0
        if reconcile_to_current_totals is not None:
            # Legacy opening stock changes the baseline, not the recorded event deltas.
            opening_adjustment = int(reconcile_to_current_totals.get(key, 0)) - observed_latest
        running_count = opening_count + opening_adjustment
        if not timestamp_events and running_count == 0:
            continue

        points = [
            {
                "x": min_x,
                "y": running_count,
                "before": running_count,
                "delta": 0,
                "kind": "opening",
                "events": [],
                "highlight_missing": False,
            }
        ]
        for event_at, events in timestamp_events.items():
            delta = sum(event["delta"] for event in events)
            before = running_count
            running_count += delta
            points.append(
                {
                    "x": _stock_timestamp_ms(event_at),
                    "y": running_count,
                    "before": before,
                    "delta": delta,
                    "kind": "event",
                    "events": events,
                    "highlight_missing": any(event["highlight_missing"] for event in events),
                }
            )
        points.append(
            {
                "x": max_x,
                "y": running_count,
                "before": running_count,
                "delta": 0,
                "kind": "closing",
                "events": [],
                "highlight_missing": False,
            }
        )
        label = _stock_series_label(group_by_fields=group_by_fields, group_key=key)
        datasets.append(
            {"label": label, "points": points, "opening_adjustment": opening_adjustment}
        )
        latest_totals.append({"label": label, "head_count": running_count})

    latest_totals.sort(key=lambda row: (-row["head_count"], row["label"].lower()))
    return {
        "datasets": datasets,
        "min_x": min_x,
        "max_x": max_x,
        "time_zone": "UTC",
    }, latest_totals


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
