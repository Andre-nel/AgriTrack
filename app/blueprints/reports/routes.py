from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    GrazingSession,
    JournalEntry,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
    RainfallRecord,
    StockLedgerEntry,
    WaterAsset,
    WaterConnection,
)
from app.models.stock_ledger import StockEventType
from app.services.farm_import_service import FarmImportService
from app.services.mob_event_service import MobEventService
from app.services.grazing_history_service import GrazingHistoryService
from app.services.movement_service import MovementService
from app.services.paddock_service import PaddockService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService
from app.services.water_network_service import WaterNetworkService

bp = Blueprint("web", __name__)

ANALYTICS_GROUP_LABELS = {
    "species": "Species",
    "breed": "Breed",
    "sex": "Sex",
    "age_class": "Age Class",
    "farm": "Farm",
}
ANALYTICS_GROUP_ORDER = ("species", "breed", "sex", "age_class", "farm")
ANALYTICS_DEFAULT_GROUP_BY = ("species",)
ANALYTICS_STOCK_IN_TYPES = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}
ANALYTICS_STOCK_OUT_TYPES = {
    StockEventType.death,
    StockEventType.sale,
    StockEventType.missing,
    StockEventType.transfer_out,
    StockEventType.adjustment_out,
}
GRAZING_ANALYTICS_METRIC_LABELS = {
    "current_lsu": "Current LSU",
    "lsu_per_ha": "LSU/ha",
    "ha_per_lsu": "ha/LSU",
    "pressure_pct": "Pressure (%)",
}
GRAZING_ANALYTICS_METRIC_AXIS_LABELS = {
    "current_lsu": "LSU",
    "lsu_per_ha": "LSU/ha",
    "ha_per_lsu": "ha/LSU",
    "pressure_pct": "Pressure (%)",
}
GRAZING_ANALYTICS_METRIC_ORDER = ("current_lsu", "lsu_per_ha", "ha_per_lsu", "pressure_pct")
GRAZING_ANALYTICS_DEFAULT_METRICS = ("current_lsu",)
GRAZING_ANALYTICS_SPLIT_MODES = {"metric", "paddock"}
GRAZING_ANALYTICS_POINT_FILTER_OPERATORS = {
    "gt": "Greater Than",
    "lt": "Less Than",
}
GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES = {
    "has_any": "Has Matching Points",
    "has_none": "Has No Matching Points",
}


def _parse_paddock_lines(raw: str) -> list[dict]:
    items = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        name = parts[0]
        area = float(parts[1]) if len(parts) > 1 and parts[1] else 0
        grazeable = float(parts[2]) if len(parts) > 2 and parts[2] else area
        items.append({"name": name, "area_ha": area, "grazeable_area_ha": grazeable})
    return items


def _parse_mob_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _parse_placements(raw: str) -> list[tuple[str, str]]:
    placements = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            placements.append((parts[0], parts[1]))
    return placements


def _parse_query_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _normalize_name(value: str | None) -> str:
    return PaddockService.normalize_name(value).lower()


def _round_float(value: float | None, precision: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), precision)


def _water_asset_form_payload(form, *, farm_id: str) -> dict:
    return {
        "farm_id": farm_id,
        "name": form.get("name"),
        "asset_type": form.get("asset_type"),
        "active": form.get("active", "0"),
        "needs_review": form.get("needs_review", "0"),
        "location_paddock_id": form.get("location_paddock_id"),
        "latitude": form.get("latitude"),
        "longitude": form.get("longitude"),
        "altitude_m": form.get("altitude_m"),
        "status": form.get("status"),
        "water_level": form.get("water_level"),
        "capacity_m3": form.get("capacity_m3"),
        "material": form.get("material"),
        "windmill_size_ft": form.get("windmill_size_ft"),
        "solar_brand": form.get("solar_brand"),
        "solar_kw": form.get("solar_kw"),
        "solar_head_m": form.get("solar_head_m"),
        "trough_size": form.get("trough_size"),
        "source_system": form.get("source_system"),
        "served_paddock_ids": form.getlist("served_paddock_ids"),
    }


def _water_connection_form_payload(form, *, farm_id: str) -> dict:
    return {
        "farm_id": farm_id,
        "active": form.get("active", "0"),
        "flow_type": form.get("flow_type"),
        "source_asset_id": form.get("source_asset_id"),
        "destination_asset_id": form.get("destination_asset_id"),
        "pump_asset_id": form.get("pump_asset_id"),
        "pipe_material": form.get("pipe_material"),
        "pipe_diameter_spec": form.get("pipe_diameter_spec"),
        "pipe_wall_thickness_spec": form.get("pipe_wall_thickness_spec"),
        "pipe_class_spec": form.get("pipe_class_spec"),
        "pipe_quality_spec": form.get("pipe_quality_spec"),
        "notes": form.get("notes"),
    }


def _normalize_water_asset_type_filters(raw_values: list[str], *, filters_applied: bool) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in WaterNetworkService.ASSET_TYPES or value in seen:
            continue
        selected.append(value)
        seen.add(value)

    if filters_applied:
        return selected
    return list(WaterNetworkService.ASSET_TYPES)


def _active_mobs_for_farm(farm_id: str) -> list[Mob]:
    return Mob.query.filter_by(farm_id=farm_id, status="active").order_by(Mob.name).all()


def _get_active_mob_or_404(mob_id: str) -> Mob:
    return Mob.query.filter_by(id=mob_id, status="active").first_or_404()


def _normalize_analytics_group_by(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in ANALYTICS_GROUP_LABELS or value in seen:
            continue
        selected.append(value)
        seen.add(value)

    if not selected:
        return list(ANALYTICS_DEFAULT_GROUP_BY)
    return selected


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


def _current_stock_totals_by_group(
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


def _build_stock_tracking_chart_data(
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


def _normalize_grazing_metric_selection(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in GRAZING_ANALYTICS_METRIC_LABELS or value in seen:
            continue
        selected.append(value)
        seen.add(value)
    if not selected:
        return list(GRAZING_ANALYTICS_DEFAULT_METRICS)
    return selected


def _normalize_grazing_split_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_SPLIT_MODES:
        return "metric"
    return candidate


def _normalize_grazing_point_filter_metric(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_METRIC_LABELS:
        return GRAZING_ANALYTICS_DEFAULT_METRICS[0]
    return candidate


def _normalize_grazing_point_filter_operator(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_POINT_FILTER_OPERATORS:
        return "gt"
    return candidate


def _normalize_grazing_point_filter_match_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES:
        return "has_any"
    return candidate


def _paddock_matches_grazing_point_filter(
    *,
    paddock_payload: dict,
    metric: str,
    operator: str,
    threshold_value: float,
) -> bool:
    values = paddock_payload.get("metrics", {}).get(metric, [])
    if operator == "lt":
        return any(value is not None and float(value) < threshold_value for value in values)
    return any(value is not None and float(value) > threshold_value for value in values)


def _filter_grazing_paddocks_by_point_rule(
    paddocks: list[Paddock],
    analytics_payload: dict,
    *,
    metric: str,
    operator: str,
    threshold_value: float | None,
    match_mode: str,
) -> list[Paddock]:
    if threshold_value is None:
        return list(paddocks)

    filtered = []
    for paddock in paddocks:
        paddock_payload = analytics_payload["paddocks"].get(str(paddock.id), {})
        has_match = _paddock_matches_grazing_point_filter(
            paddock_payload=paddock_payload,
            metric=metric,
            operator=operator,
            threshold_value=threshold_value,
        )
        if match_mode == "has_none":
            if not has_match:
                filtered.append(paddock)
            continue
        if has_match:
            filtered.append(paddock)
    return filtered


def _grazing_paddock_series_label(paddock: Paddock, include_farm_name: bool) -> str:
    if include_farm_name:
        return f"{paddock.farm.name} | {paddock.name}"
    return paddock.name


def _flatten_grazing_periods(
    paddocks: list[Paddock],
    analytics_payload: dict,
) -> tuple[list[dict], dict | None]:
    periods = []
    for paddock in paddocks:
        paddock_id = str(paddock.id)
        paddock_payload = analytics_payload["paddocks"].get(paddock_id, {})
        for period in paddock_payload.get("periods", []):
            periods.append(
                {
                    "paddock_id": paddock_id,
                    "paddock_name": paddock.name,
                    "period": period,
                }
            )
    initial_period = periods[0] if periods else None
    return periods, initial_period


def _build_grazing_chart_panels(
    *,
    paddocks: list[Paddock],
    analytics_payload: dict,
    selected_metrics: list[str],
    split_mode: str,
) -> list[dict]:
    if not paddocks:
        return []

    panels = []
    include_farm_name = len({str(paddock.farm_id) for paddock in paddocks}) > 1

    if split_mode == "metric":
        for metric in selected_metrics:
            datasets = []
            for paddock in paddocks:
                paddock_payload = analytics_payload["paddocks"].get(str(paddock.id), {})
                datasets.append(
                    {
                        "key": f"paddock:{paddock.id}",
                        "label": _grazing_paddock_series_label(paddock, include_farm_name),
                        "values": paddock_payload.get("metrics", {}).get(metric, []),
                    }
                )
            panels.append(
                {
                    "id": f"metric-{metric}",
                    "title": GRAZING_ANALYTICS_METRIC_LABELS[metric],
                    "metric": metric,
                    "y_axis_label": GRAZING_ANALYTICS_METRIC_AXIS_LABELS[metric],
                    "datasets": datasets,
                }
            )
        return panels

    for paddock in paddocks:
        paddock_payload = analytics_payload["paddocks"].get(str(paddock.id), {})
        metric_panels = []
        for metric in selected_metrics:
            metric_panels.append(
                {
                    "id": f"paddock-{paddock.id}-{metric}",
                    "title": GRAZING_ANALYTICS_METRIC_LABELS[metric],
                    "metric": metric,
                    "y_axis_label": GRAZING_ANALYTICS_METRIC_AXIS_LABELS[metric],
                    "datasets": [
                        {
                            "key": f"metric:{metric}",
                            "label": GRAZING_ANALYTICS_METRIC_LABELS[metric],
                            "values": paddock_payload.get("metrics", {}).get(metric, []),
                        }
                    ],
                }
            )
        panels.append(
            {
                "id": f"paddock-{paddock.id}",
                "group_title": _grazing_paddock_series_label(paddock, include_farm_name),
                "metric_panels": metric_panels,
            }
        )
    return panels


def _grazing_uncovered_paddocks_in_range(
    paddocks: list[Paddock],
    analytics_payload: dict,
    *,
    start_date: date,
) -> list[str]:
    uncovered = []
    for paddock in paddocks:
        coverage = analytics_payload["paddocks"].get(str(paddock.id), {}).get("coverage", {})
        earliest_session = coverage.get("earliest_session")
        earliest_history = coverage.get("earliest_history")
        if earliest_session is None:
            continue
        if earliest_history is None:
            uncovered.append(paddock.name)
            continue
        if start_date < earliest_history.date() and earliest_session < earliest_history:
            uncovered.append(paddock.name)
    return uncovered


def _normalize_journal_tag(value: str | None) -> str:
    return " ".join((value or "").replace("_", " ").strip().lower().split())


def _normalize_journal_tags(values: list[str]) -> list[str]:
    tags = []
    seen = set()
    for value in values:
        tag = _normalize_journal_tag(value)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def _normalize_journal_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _build_analytics_journal_entries(
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
            tags = _normalize_journal_tags(["journal", f"farm {farm_name}"])
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
            tags = _normalize_journal_tags(
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

        tags = _normalize_journal_tags(
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

        tags = _normalize_journal_tags(
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

        tags = _normalize_journal_tags(
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


def _group_analytics_journal_entries(
    entries: list[dict],
    selected_tag: str,
) -> list[dict]:
    tag_filter = _normalize_journal_tag(selected_tag)
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


def _parse_kml_ring(raw: str | None) -> list[list[float]]:
    coords = []
    for token in (raw or "").replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append([lon, lat])

    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _placemark_geometry(placemark, namespace: dict[str, str]) -> dict | None:
    polygons = []
    for polygon in placemark.findall(".//k:Polygon", namespace):
        ring_text = polygon.findtext(
            ".//k:outerBoundaryIs/k:LinearRing/k:coordinates",
            default="",
            namespaces=namespace,
        )
        ring = _parse_kml_ring(ring_text)
        if len(ring) >= 4:
            polygons.append([ring])

    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _load_farm_kml_features(farm_name: str) -> tuple[list[dict], str]:
    kml_path = Path(current_app.instance_path) / "maps" / f"{farm_name}.kml"
    if not kml_path.exists():
        raise FileNotFoundError(kml_path)

    namespace = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(kml_path).getroot()
    features = []
    for placemark in root.findall(".//k:Placemark", namespace):
        name = placemark.findtext("k:name", default="", namespaces=namespace).strip()
        geometry = _placemark_geometry(placemark, namespace)
        if not name or geometry is None:
            continue
        features.append(
            {
                "name": name,
                "normalized_name": _normalize_name(name),
                "geometry": geometry,
            }
        )

    return features, str(kml_path)


def _active_grazing_snapshot_by_paddock(farm_id: str) -> dict[str, dict]:
    rows = (
        GrazingAllocation.query.join(GrazingSession)
        .filter(
            GrazingSession.farm_id == farm_id,
            GrazingSession.end_at.is_(None),
        )
        .all()
    )

    by_paddock = {}
    for allocation in rows:
        paddock_key = str(allocation.paddock_id)
        snapshot = by_paddock.setdefault(
            paddock_key,
            {"current_lsu": 0.0, "mobs": [], "species_heads": {}},
        )

        fraction = float(allocation.allocation_fraction)
        mob = allocation.grazing_session.mob
        allocated_lsu = ReportingService.mob_total_lsu(mob) * fraction
        snapshot["current_lsu"] += allocated_lsu
        snapshot["mobs"].append(
            {
                "mob_id": str(mob.id),
                "mob_name": mob.name,
                "allocation_pct": _round_float(fraction * 100.0, 2),
                "allocated_lsu": _round_float(allocated_lsu, 3),
            }
        )

        for balance in mob.balances:
            species = balance.animal_group_type.species
            current_head = snapshot["species_heads"].get(species, 0.0)
            snapshot["species_heads"][species] = current_head + (float(balance.head_count) * fraction)

    for snapshot in by_paddock.values():
        snapshot["mobs"].sort(key=lambda item: item["allocated_lsu"], reverse=True)
        species_rows = [
            {"species": species, "head": _round_float(head, 2)}
            for species, head in sorted(snapshot["species_heads"].items(), key=lambda row: row[0])
        ]
        snapshot["species_heads"] = species_rows
        snapshot["current_lsu"] = _round_float(snapshot["current_lsu"], 3)

    return by_paddock


def _paddock_map_properties(paddock: Paddock, farm: Farm, active_snapshot: dict) -> dict:
    today = date.today()
    now_dt = datetime.utcnow()
    year_start_dt = datetime(today.year, 1, 1)

    grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
    area_ha = float(paddock.area_ha or 0)
    effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha

    effective_stocking_rate = float(
        paddock.stocking_rate_ha_per_lsu_override or farm.default_stocking_rate_ha_per_lsu
    )
    grazing_capacity_sdh = (365.0 / effective_stocking_rate) if effective_stocking_rate > 0 else None

    year_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
        paddock,
        period_start=year_start_dt,
        period_end=now_dt,
    )
    sdh_used_this_year = (year_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    grazing_pressure_ratio = (
        (sdh_used_this_year / grazing_capacity_sdh)
        if sdh_used_this_year is not None and grazing_capacity_sdh
        else None
    )

    active = active_snapshot.get(str(paddock.id), {})
    current_lsu = active.get("current_lsu", 0.0)
    paddock_ha_per_current_lsu = (area_ha / current_lsu) if current_lsu > 0 else None
    current_activity = ReportingService.paddock_continuous_activity(paddock)
    return {
        "feature_type": "paddock",
        "farm_id": str(farm.id),
        "farm_name": farm.name,
        "paddock_id": str(paddock.id),
        "paddock_url": url_for("web.paddock_detail", paddock_id=paddock.id),
        "name": paddock.name,
        "status": paddock.status,
        "area_ha": _round_float(area_ha, 2),
        "grazeable_area_ha": _round_float(grazeable_area_ha, 2),
        "effective_area_ha": _round_float(effective_area_ha, 2),
        "effective_stocking_rate_ha_per_lsu": _round_float(effective_stocking_rate, 2),
        "grazing_capacity_sdh": _round_float(grazing_capacity_sdh, 4),
        "sdh_used_this_year": _round_float(sdh_used_this_year, 4),
        "grazing_pressure_ratio": _round_float(grazing_pressure_ratio, 4),
        "current_lsu": current_lsu,
        "paddock_ha_per_current_lsu": _round_float(paddock_ha_per_current_lsu, 2),
        "current_activity_state": current_activity["current_activity_state"],
        "current_activity_label": current_activity["current_activity_label"],
        "current_activity_days": _round_float(current_activity["current_activity_days"], 2),
        "days_grazed_continuously": _round_float(
            current_activity["days_grazed_continuously"], 2
        ),
        "days_rested_continuously": _round_float(
            current_activity["days_rested_continuously"], 2
        ),
        "mobs": active.get("mobs", []),
        "species_heads": active.get("species_heads", []),
    }


def _build_farm_map_feature_collection(
    farm: Farm,
    *,
    include_water: bool = False,
    allow_missing_kml: bool = False,
) -> dict:
    expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
    if not expected_kml_path.exists() and not allow_missing_kml:
        raise FileNotFoundError(expected_kml_path)

    kml_features = []
    warnings = []
    if expected_kml_path.exists():
        kml_features, kml_path = _load_farm_kml_features(farm.name)
    else:
        kml_path = str(expected_kml_path)
        warnings.append(f"Farm map KML file not found: {expected_kml_path}")
    paddocks = list(Paddock.query.filter_by(farm_id=farm.id, status="active").all())
    paddock_by_name = {_normalize_name(p.name): p for p in paddocks}
    farm_name_key = _normalize_name(farm.name)
    active_snapshot = _active_grazing_snapshot_by_paddock(str(farm.id))

    feature_collection = []
    matched_paddock_ids = set()
    unmatched_placemarks = []

    for item in kml_features:
        normalized_name = item["normalized_name"]
        paddock = paddock_by_name.get(normalized_name)
        if paddock:
            properties = _paddock_map_properties(paddock, farm, active_snapshot)
            matched_paddock_ids.add(str(paddock.id))
        else:
            feature_type = "farm_boundary" if normalized_name == farm_name_key else "unmatched"
            properties = {
                "feature_type": feature_type,
                "farm_id": str(farm.id),
                "farm_name": farm.name,
                "name": item["name"],
            }
            if feature_type == "unmatched":
                unmatched_placemarks.append(item["name"])

        feature_collection.append(
            {
                "type": "Feature",
                "geometry": item["geometry"],
                "properties": properties,
            }
        )

    paddocks_without_kml = [
        p.name for p in sorted(paddocks, key=lambda row: row.name.lower()) if str(p.id) not in matched_paddock_ids
    ]
    if include_water:
        water_features = WaterNetworkService.active_map_features_for_farm(str(farm.id))
        for feature in water_features:
            properties = feature.get("properties", {})
            if properties.get("feature_type") == "water_asset" and properties.get("id"):
                properties["asset_workspace_url"] = url_for("web.farm_water_workspace", farm_id=farm.id)
        feature_collection.extend(water_features)
        if warnings and feature_collection:
            warnings.append("Showing water network features without farm KML polygons.")

    return {
        "type": "FeatureCollection",
        "farm_id": str(farm.id),
        "farm_name": farm.name,
        "kml_path": kml_path,
        "metric": "grazing_pressure_ratio",
        "generated_at": f"{datetime.utcnow().isoformat()}Z",
        "unmatched_placemarks": unmatched_placemarks,
        "paddocks_without_kml": paddocks_without_kml,
        "warnings": warnings,
        "features": feature_collection,
    }


@bp.get("/")
def dashboard():
    summary = ReportingService.dashboard_summary()
    farms = Farm.query.order_by(Farm.name).all()

    farm_cards = []
    for farm in farms:
        paddocks = len([paddock for paddock in farm.paddocks if paddock.status == "active"])
        mobs = len([mob for mob in farm.mobs if mob.status == "active"])
        ready = paddocks > 0 and mobs > 0
        farm_cards.append(
            {
                "id": str(farm.id),
                "name": farm.name,
                "timezone": farm.timezone,
                "paddock_count": paddocks,
                "mob_count": mobs,
                "rainfall_count": len(farm.rainfall_records),
                "ready": ready,
            }
        )

    return render_template("dashboard.html", summary=summary, farm_cards=farm_cards)


@bp.get("/analytics")
def analytics_landing():
    return render_template("analytics/index.html")


@bp.get("/analytics/grazing-management")
def analytics_grazing_management():
    selected_filters = {
        "farm_id": (request.args.get("farm_id") or "").strip(),
    }
    requested_paddock_ids = {
        (value or "").strip() for value in request.args.getlist("paddock_id") if (value or "").strip()
    }
    selected_metrics = _normalize_grazing_metric_selection(request.args.getlist("metric"))
    split_mode = _normalize_grazing_split_mode(request.args.get("split_mode"))
    point_filter = {
        "metric": _normalize_grazing_point_filter_metric(request.args.get("point_filter_metric")),
        "operator": _normalize_grazing_point_filter_operator(request.args.get("point_filter_operator")),
        "match_mode": _normalize_grazing_point_filter_match_mode(
            request.args.get("point_filter_match_mode")
        ),
        "raw_value": (request.args.get("point_filter_value") or "").strip(),
        "value": None,
        "is_active": False,
        "summary": None,
    }
    today = date.today()

    try:
        end_date = _parse_query_date(request.args.get("end_date")) or today
        start_date = _parse_query_date(request.args.get("start_date"))
    except ValueError:
        flash("Grazing Management dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.analytics_grazing_management"))

    if point_filter["raw_value"]:
        try:
            point_filter["value"] = float(Decimal(point_filter["raw_value"]))
            point_filter["is_active"] = True
        except (InvalidOperation, ValueError):
            flash("Trend chart filter value must be a valid number", "error")
            return redirect(url_for("web.analytics_grazing_management"))

    if start_date is not None and end_date < start_date:
        flash("Grazing Management end date must be on or after the start date", "error")
        return redirect(url_for("web.analytics_grazing_management"))

    farms = Farm.query.order_by(Farm.name).all()
    paddock_query = Paddock.query.join(Farm).order_by(Farm.name.asc(), Paddock.name.asc())
    if selected_filters["farm_id"]:
        paddock_query = paddock_query.filter(Paddock.farm_id == selected_filters["farm_id"])
    scope_paddocks = paddock_query.all()
    valid_scope_ids = {str(paddock.id) for paddock in scope_paddocks}

    if requested_paddock_ids:
        selected_paddocks = [
            paddock for paddock in scope_paddocks if str(paddock.id) in requested_paddock_ids & valid_scope_ids
        ]
        if not selected_paddocks:
            selected_paddocks = scope_paddocks
    else:
        selected_paddocks = scope_paddocks

    earliest_covered_row = None
    if selected_paddocks:
        earliest_covered_row = (
            db.session.query(func.min(GrazingAllocationLsuHistory.effective_from))
            .filter(GrazingAllocationLsuHistory.paddock_id.in_([str(paddock.id) for paddock in selected_paddocks]))
            .scalar()
        )

    if start_date is None:
        rolling_start = today - timedelta(days=364)
        if earliest_covered_row is not None:
            start_date = max(rolling_start, earliest_covered_row.date())
        else:
            start_date = rolling_start

    analytics_payload = GrazingHistoryService.build_paddock_daily_metrics(
        selected_paddocks,
        start_date=start_date,
        end_date=end_date,
    )
    visible_paddocks = _filter_grazing_paddocks_by_point_rule(
        selected_paddocks,
        analytics_payload,
        metric=point_filter["metric"],
        operator=point_filter["operator"],
        threshold_value=point_filter["value"],
        match_mode=point_filter["match_mode"],
    )
    if point_filter["is_active"]:
        metric_label = GRAZING_ANALYTICS_METRIC_LABELS[point_filter["metric"]]
        operator_label = GRAZING_ANALYTICS_POINT_FILTER_OPERATORS[point_filter["operator"]].lower()
        match_text = (
            "with any matching points"
            if point_filter["match_mode"] == "has_any"
            else "with no matching points"
        )
        point_filter["summary"] = (
            f"Showing {len(visible_paddocks)} of {len(selected_paddocks)} paddock(s) {match_text} "
            f"for {metric_label} {operator_label} {point_filter['raw_value']} in the selected date range."
        )

    timeline_periods, initial_period = _flatten_grazing_periods(visible_paddocks, analytics_payload)
    chart_panels = _build_grazing_chart_panels(
        paddocks=visible_paddocks,
        analytics_payload=analytics_payload,
        selected_metrics=selected_metrics,
        split_mode=split_mode,
    )
    uncovered_paddocks = _grazing_uncovered_paddocks_in_range(
        visible_paddocks,
        analytics_payload,
        start_date=start_date,
    )

    include_farm_name = len({str(paddock.farm_id) for paddock in scope_paddocks}) > 1
    filter_options = {
        "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
        "paddocks": [
            {
                "id": str(paddock.id),
                "label": _grazing_paddock_series_label(paddock, include_farm_name),
                "farm_name": paddock.farm.name,
            }
            for paddock in scope_paddocks
        ],
        "metrics": [
            {"value": metric, "label": GRAZING_ANALYTICS_METRIC_LABELS[metric]}
            for metric in GRAZING_ANALYTICS_METRIC_ORDER
        ],
        "point_filter_operators": [
            {"value": value, "label": label}
            for value, label in GRAZING_ANALYTICS_POINT_FILTER_OPERATORS.items()
        ],
        "point_filter_match_modes": [
            {"value": value, "label": label}
            for value, label in GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES.items()
        ],
        "split_modes": [
            {"value": "metric", "label": "Split By Metric"},
            {"value": "paddock", "label": "Split By Paddock"},
        ],
    }
    timeline_rows = [
        {
            "paddock_id": str(paddock.id),
            "paddock_name": _grazing_paddock_series_label(paddock, include_farm_name),
            "segments": analytics_payload["paddocks"].get(str(paddock.id), {}).get("periods", []),
        }
        for paddock in visible_paddocks
    ]
    chart_payload = {
        "labels": analytics_payload["labels"],
        "split_mode": split_mode,
        "panels": chart_panels,
    }

    return render_template(
        "analytics/grazing_management.html",
        filter_options=filter_options,
        selected_filters=selected_filters,
        selected_paddock_ids=[str(paddock.id) for paddock in selected_paddocks],
        selected_metrics=selected_metrics,
        split_mode=split_mode,
        point_filter=point_filter,
        start_date=start_date,
        end_date=end_date,
        chart_payload=chart_payload,
        timeline_rows=timeline_rows,
        timeline_ticks=analytics_payload["ticks"],
        initial_period=initial_period,
        uncovered_paddocks=uncovered_paddocks,
        total_periods=len(timeline_periods),
        selected_paddock_count=len(selected_paddocks),
        visible_paddock_count=len(visible_paddocks),
    )


@bp.get("/analytics/stock-tracking")
def analytics_stock_tracking():
    selected_filters = {
        "farm_id": (request.args.get("farm_id") or "").strip(),
        "species": (request.args.get("species") or "").strip(),
        "breed": (request.args.get("breed") or "").strip(),
        "sex": (request.args.get("sex") or "").strip(),
        "age_class": (request.args.get("age_class") or "").strip(),
    }
    selected_group_by = _normalize_analytics_group_by(request.args.getlist("group_by"))
    today = date.today()

    try:
        start_date = _parse_query_date(request.args.get("start_date"))
        end_date = _parse_query_date(request.args.get("end_date")) or today
    except ValueError:
        flash("Analytics dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.analytics_stock_tracking"))

    if start_date and end_date < start_date:
        flash("Analytics end date must be on or after the start date", "error")
        return redirect(url_for("web.analytics_stock_tracking"))

    farms = Farm.query.order_by(Farm.name).all()
    group_types = AnimalGroupType.query.order_by(
        AnimalGroupType.species,
        AnimalGroupType.breed,
        AnimalGroupType.sex,
        AnimalGroupType.age_class,
    ).all()
    filter_options = {
        "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
        "species": sorted({group.species for group in group_types}),
        "breed": sorted({group.breed for group in group_types}),
        "sex": sorted({group.sex for group in group_types}),
        "age_class": sorted({group.age_class for group in group_types}),
    }
    group_by_options = [
        {"value": field, "label": ANALYTICS_GROUP_LABELS[field]}
        for field in ANALYTICS_GROUP_ORDER
    ]

    ledger_query = (
        db.session.query(StockLedgerEntry, Farm.name.label("farm_name"), AnimalGroupType)
        .join(Farm, StockLedgerEntry.farm_id == Farm.id)
        .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
    )

    if selected_filters["farm_id"]:
        ledger_query = ledger_query.filter(StockLedgerEntry.farm_id == selected_filters["farm_id"])
    if selected_filters["species"]:
        ledger_query = ledger_query.filter(AnimalGroupType.species == selected_filters["species"])
    if selected_filters["breed"]:
        ledger_query = ledger_query.filter(AnimalGroupType.breed == selected_filters["breed"])
    if selected_filters["sex"]:
        ledger_query = ledger_query.filter(AnimalGroupType.sex == selected_filters["sex"])
    if selected_filters["age_class"]:
        ledger_query = ledger_query.filter(AnimalGroupType.age_class == selected_filters["age_class"])

    period_end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    ledger_rows = (
        ledger_query.filter(StockLedgerEntry.event_time < period_end_dt)
        .order_by(StockLedgerEntry.event_time.asc(), StockLedgerEntry.id.asc())
        .all()
    )

    if start_date is None:
        if ledger_rows:
            start_date = ledger_rows[0][0].event_time.date()
        else:
            start_date = end_date - timedelta(days=30)

    reconcile_to_current_totals = None
    if end_date >= today:
        current_totals = _current_stock_totals_by_group(
            selected_filters=selected_filters,
            group_by_fields=selected_group_by,
        )
        if current_totals:
            reconcile_to_current_totals = current_totals

    chart_data, latest_totals = _build_stock_tracking_chart_data(
        ledger_rows=ledger_rows,
        group_by_fields=selected_group_by,
        start_date=start_date,
        end_date=end_date,
        reconcile_to_current_totals=reconcile_to_current_totals,
    )

    return render_template(
        "analytics/stock_tracking.html",
        filter_options=filter_options,
        selected_filters=selected_filters,
        group_by_options=group_by_options,
        selected_group_by=selected_group_by,
        start_date=start_date,
        end_date=end_date,
        chart_data=chart_data,
        latest_totals=latest_totals,
    )


@bp.get("/analytics/journal")
def analytics_journal():
    selected_filters = {
        "farm_id": (request.args.get("farm_id") or "").strip(),
        "tag": (request.args.get("tag") or "").strip(),
    }
    today = date.today()

    try:
        start_date = _parse_query_date(request.args.get("start_date")) or (today - timedelta(days=30))
        end_date = _parse_query_date(request.args.get("end_date")) or today
    except ValueError:
        flash("Journal dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.analytics_journal"))

    if end_date < start_date:
        flash("Journal end date must be on or after the start date", "error")
        return redirect(url_for("web.analytics_journal"))

    farms = Farm.query.order_by(Farm.name).all()
    filter_options = {
        "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
    }

    entries = _build_analytics_journal_entries(
        farm_id=selected_filters["farm_id"],
        start_date=start_date,
        end_date=end_date,
    )
    journal_days = _group_analytics_journal_entries(
        entries=entries,
        selected_tag=selected_filters["tag"],
    )
    total_entries = sum(len(day["entries"]) for day in journal_days)

    return render_template(
        "analytics/journal.html",
        filter_options=filter_options,
        selected_filters=selected_filters,
        start_date=start_date,
        end_date=end_date,
        journal_new_entry_event_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M"),
        journal_days=journal_days,
        total_entries=total_entries,
    )


@bp.post("/analytics/journal")
def analytics_journal_create_form():
    farm_id = (request.form.get("farm_id") or "").strip()
    tags_raw = request.form.get("tags")
    description = (request.form.get("description") or "").strip()
    event_at_raw = (request.form.get("event_at") or "").strip()

    redirect_kwargs = {}
    return_farm_id = (request.form.get("return_farm_id") or "").strip()
    return_tag = (request.form.get("return_tag") or "").strip()
    return_start_date = (request.form.get("return_start_date") or "").strip()
    return_end_date = (request.form.get("return_end_date") or "").strip()
    if return_farm_id:
        redirect_kwargs["farm_id"] = return_farm_id
    if return_tag:
        redirect_kwargs["tag"] = return_tag
    if return_start_date:
        redirect_kwargs["start_date"] = return_start_date
    if return_end_date:
        redirect_kwargs["end_date"] = return_end_date

    farm = Farm.query.filter_by(id=farm_id).first()
    if not farm:
        flash("Journal entry farm is required", "error")
        return redirect(url_for("web.analytics_journal", **redirect_kwargs))

    try:
        tags = MobEventService.parse_tags(tags_raw)
        if not description:
            raise ValueError("Description is required")
        if len(description) > MobEventService.MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be {MobEventService.MAX_DESCRIPTION_LENGTH} characters or fewer"
            )

        if event_at_raw:
            event_at = datetime.fromisoformat(event_at_raw)
            if event_at.tzinfo is None:
                event_at = event_at.replace(tzinfo=timezone.utc)
        else:
            event_at = datetime.now(timezone.utc)

        db.session.add(
            JournalEntry(
                farm_id=farm.id,
                event_at=event_at,
                tags_csv=MobEventService.tags_to_csv(tags),
                description=description,
            )
        )
        db.session.commit()
        flash("Journal entry recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.analytics_journal", **redirect_kwargs))


@bp.route("/setup", methods=["GET", "POST"])
def setup_farm():
    if request.method == "GET":
        return render_template("setup.html")

    farm_name = (request.form.get("farm_name") or "").strip()
    timezone = (request.form.get("timezone") or "UTC").strip() or "UTC"
    paddocks_raw = request.form.get("paddocks") or ""
    mobs_raw = request.form.get("mobs") or ""
    placements_raw = request.form.get("placements") or ""

    if not farm_name:
        flash("Farm name is required", "error")
        return redirect(url_for("web.setup_farm"))

    try:
        farm = Farm(name=farm_name, timezone=timezone, active=True)
        db.session.add(farm)
        db.session.flush()

        paddock_map = {}
        mob_map = {}

        for p in _parse_paddock_lines(paddocks_raw):
            paddock_name = PaddockService.validate_available_name(str(farm.id), p["name"])
            paddock = Paddock(
                farm_id=farm.id,
                name=paddock_name,
                area_ha=p["area_ha"],
                grazeable_area_ha=p["grazeable_area_ha"],
            )
            db.session.add(paddock)
            db.session.flush()
            paddock_map[paddock.name.lower()] = paddock

        for name in _parse_mob_lines(mobs_raw):
            mob = Mob(farm_id=farm.id, name=name, status="active")
            db.session.add(mob)
            db.session.flush()
            mob_map[mob.name.lower()] = mob

        for mob_name, paddock_name in _parse_placements(placements_raw):
            mob = mob_map.get(mob_name.lower())
            paddock = paddock_map.get(paddock_name.lower())
            if not mob or not paddock:
                continue
            MovementService.move_mob(
                mob=mob,
                allocations=[{"paddock_id": paddock.id, "allocation_fraction": "1.0"}],
            )

        db.session.commit()
        flash("Farm setup complete", "success")
        return redirect(url_for("web.farm_detail", farm_id=farm.id))
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(f"Setup failed: {exc}", "error")
        return redirect(url_for("web.setup_farm"))


@bp.route("/farms", methods=["GET", "POST"])
def farms_page():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        timezone = (request.form.get("timezone") or "UTC").strip() or "UTC"
        if not name:
            flash("Farm name is required", "error")
            return redirect(url_for("web.farms_page"))

        db.session.add(Farm(name=name, timezone=timezone, active=True))
        db.session.commit()
        flash("Farm created", "success")
        return redirect(url_for("web.farms_page"))

    farms = Farm.query.order_by(Farm.name).all()
    return render_template("farms.html", farms=farms)


@bp.route("/farms/import", methods=["GET", "POST"])
def import_farm_page():
    if request.method == "GET":
        return render_template("import_farm.html", default_timezone="UTC")

    uploaded_file = request.files.get("farm_kml")
    file_name = uploaded_file.filename if uploaded_file else ""
    file_bytes = uploaded_file.read() if uploaded_file else b""
    timezone_value = (request.form.get("timezone") or "UTC").strip() or "UTC"

    try:
        result = FarmImportService.import_farm(
            file_name=file_name,
            file_bytes=file_bytes,
            timezone=timezone_value,
            instance_path=current_app.instance_path,
            managed_point_hints=request.form.get("water_managed_ids_json"),
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("web.import_farm_page"))

    if result["existing_farm"]:
        retired_note = (
            f", {result['retired_count']} retired"
            if result.get("retired_count")
            else ""
        )
        water_note = (
            f"; water assets {result['water_created_count']} added, {result['water_updated_count']} updated, {result['water_archived_count']} archived"
            if result.get("water_asset_count")
            else ""
        )
        flash(
            (
                f"Updated farm {result['farm_name']} from import with {result['paddock_count']} paddock(s) "
                f"({result['created_count']} added, {result['updated_count']} updated{retired_note}){water_note}"
            ),
            "success",
        )
    else:
        water_note = (
            f" and {result['water_asset_count']} water asset(s)"
            if result.get("water_asset_count")
            else ""
        )
        flash(
            f"Imported farm {result['farm_name']} with {result['paddock_count']} paddock(s){water_note}",
            "success",
        )
    return redirect(url_for("web.farm_detail", farm_id=result["farm_id"]))


@bp.get("/farms/<farm_id>")
def farm_detail(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    paddocks = sorted(
        [paddock for paddock in farm.paddocks if paddock.status == "active"],
        key=lambda p: p.name.lower(),
    )
    mobs = _active_mobs_for_farm(farm_id)
    located_mob_ids = set()
    if mobs:
        mob_ids = [mob.id for mob in mobs]
        active_allocations = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingSession.mob_id.in_(mob_ids),
                GrazingSession.end_at.is_(None),
            )
            .all()
        )
        located_mob_ids = {str(allocation.grazing_session.mob_id) for allocation in active_allocations}
    mob_detail_labels = {
        str(mob.id): ("Not Located" if str(mob.id) not in located_mob_ids else mob.status)
        for mob in mobs
    }
    farm_total_area_ha = float(sum((p.area_ha or 0) for p in paddocks))
    farm_current_lsu = sum(ReportingService.mob_total_lsu(mob) for mob in mobs)
    farm_capacity_lsu = (
        farm_total_area_ha / float(farm.default_stocking_rate_ha_per_lsu)
        if float(farm.default_stocking_rate_ha_per_lsu) > 0
        else 0.0
    )
    selected_filters = {
        "species": (request.args.get("species") or "").strip(),
        "breed": (request.args.get("breed") or "").strip(),
        "sex": (request.args.get("sex") or "").strip(),
        "age_class": (request.args.get("age_class") or "").strip(),
    }
    stock_totals = {}
    stock_mob_totals = {}
    for mob in mobs:
        mob_id = str(mob.id)
        for balance in mob.balances:
            group = balance.animal_group_type
            key = (
                group.species,
                group.breed,
                group.sex,
                group.age_class,
            )
            head_count = int(balance.head_count)
            stock_totals[key] = stock_totals.get(key, 0) + head_count

            mob_totals = stock_mob_totals.setdefault(key, {})
            mob_totals[mob_id] = {
                "mob_id": mob_id,
                "mob_name": mob.name,
                "head_count": mob_totals.get(mob_id, {}).get("head_count", 0) + head_count,
            }

    farm_stock_summary_all = [
        {
            "species": key[0],
            "breed": key[1],
            "sex": key[2],
            "age_class": key[3],
            "head_count": head_count,
            "mobs": sorted(
                stock_mob_totals.get(key, {}).values(),
                key=lambda row: (-row["head_count"], row["mob_name"].lower()),
            ),
        }
        for key, head_count in sorted(stock_totals.items(), key=lambda item: item[0])
    ]
    filter_options = {
        "species": sorted({row["species"] for row in farm_stock_summary_all}),
        "breed": sorted({row["breed"] for row in farm_stock_summary_all}),
        "sex": sorted({row["sex"] for row in farm_stock_summary_all}),
        "age_class": sorted({row["age_class"] for row in farm_stock_summary_all}),
    }
    farm_stock_summary = [
        row
        for row in farm_stock_summary_all
        if (not selected_filters["species"] or row["species"] == selected_filters["species"])
        and (not selected_filters["breed"] or row["breed"] == selected_filters["breed"])
        and (not selected_filters["sex"] or row["sex"] == selected_filters["sex"])
        and (not selected_filters["age_class"] or row["age_class"] == selected_filters["age_class"])
    ]
    farm_stock_total = sum(row["head_count"] for row in farm_stock_summary)

    rainfall = (
        RainfallRecord.query.filter_by(farm_id=farm.id)
        .order_by(RainfallRecord.recorded_on.desc())
        .limit(20)
        .all()
    )
    water_summary = WaterNetworkService.farm_summary(str(farm.id))
    return render_template(
        "farm_detail.html",
        farm=farm,
        paddocks=paddocks,
        mobs=mobs,
        rainfall=rainfall,
        farm_stock_summary=farm_stock_summary,
        farm_stock_total=farm_stock_total,
        stock_filter_options=filter_options,
        selected_stock_filters=selected_filters,
        farm_total_area_ha=farm_total_area_ha,
        farm_capacity_lsu=farm_capacity_lsu,
        farm_current_lsu=farm_current_lsu,
        mob_detail_labels=mob_detail_labels,
        water_summary=water_summary,
    )


@bp.get("/farms/<farm_id>/water")
def farm_water_workspace(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    paddocks = (
        Paddock.query.filter_by(farm_id=farm.id, status="active").order_by(Paddock.name.asc()).all()
    )
    assets = WaterNetworkService.assets_for_farm(str(farm.id))
    connections = WaterNetworkService.connections_for_farm(str(farm.id))
    water_summary = WaterNetworkService.farm_summary(str(farm.id))
    active_assets = [asset for asset in assets if asset.active]
    pump_assets = [
        asset for asset in active_assets if asset.asset_type in WaterNetworkService.PUMP_ASSET_TYPES
    ]
    map_filters_applied = (request.args.get("map_filters_applied") or "").strip() == "1"
    selected_map_asset_types = _normalize_water_asset_type_filters(
        request.args.getlist("asset_type"),
        filters_applied=map_filters_applied,
    )

    return render_template(
        "farm_water.html",
        farm=farm,
        paddocks=paddocks,
        assets=assets,
        active_assets=active_assets,
        connections=connections,
        water_summary=water_summary,
        pump_assets=pump_assets,
        water_asset_types=WaterNetworkService.ASSET_TYPES,
        water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
        water_flow_types=WaterNetworkService.FLOW_TYPES,
        water_flow_type_labels=WaterNetworkService.FLOW_TYPE_LABELS,
        status_options_by_type=WaterNetworkService.STATUS_OPTIONS_BY_TYPE,
        all_status_options=sorted(
            {option for options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.values() for option in options}
        ),
        water_level_options=WaterNetworkService.WATER_LEVEL_OPTIONS,
        trough_size_options=WaterNetworkService.TROUGH_SIZE_OPTIONS,
        material_options_by_type=WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE,
        all_material_options=sorted(
            {option for options in WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE.values() for option in options}
        ),
        map_filters_applied=map_filters_applied,
        selected_map_asset_types=selected_map_asset_types,
    )


@bp.post("/farms/<farm_id>/water/assets")
def create_water_asset_form(farm_id):
    Farm.query.get_or_404(farm_id)
    try:
        WaterNetworkService.create_asset(_water_asset_form_payload(request.form, farm_id=farm_id))
        db.session.commit()
        flash("Water asset created", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Unable to save water asset", "error")
    return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))


@bp.post("/farms/<farm_id>/water/assets/<asset_id>")
def update_water_asset_form(farm_id, asset_id):
    Farm.query.get_or_404(farm_id)
    asset = WaterAsset.query.filter_by(id=asset_id, farm_id=farm_id).first_or_404()
    try:
        WaterNetworkService.update_asset(asset, _water_asset_form_payload(request.form, farm_id=farm_id))
        db.session.commit()
        flash("Water asset updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Unable to update water asset", "error")
    return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))


@bp.post("/farms/<farm_id>/water/connections")
def create_water_connection_form(farm_id):
    Farm.query.get_or_404(farm_id)
    try:
        WaterNetworkService.create_connection(_water_connection_form_payload(request.form, farm_id=farm_id))
        db.session.commit()
        flash("Water connection created", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Unable to save water connection", "error")
    return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))


@bp.post("/farms/<farm_id>/water/connections/<connection_id>")
def update_water_connection_form(farm_id, connection_id):
    Farm.query.get_or_404(farm_id)
    connection = WaterConnection.query.filter_by(id=connection_id, farm_id=farm_id).first_or_404()
    try:
        WaterNetworkService.update_connection(
            connection,
            _water_connection_form_payload(request.form, farm_id=farm_id),
        )
        db.session.commit()
        flash("Water connection updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Unable to update water connection", "error")
    return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))


@bp.post("/farms/<farm_id>/water/connections/<connection_id>/delete")
def delete_water_connection_form(farm_id, connection_id):
    Farm.query.get_or_404(farm_id)
    connection = WaterConnection.query.filter_by(id=connection_id, farm_id=farm_id).first_or_404()
    db.session.delete(connection)
    db.session.commit()
    flash("Water connection deleted", "success")
    return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))


@bp.get("/farms/<farm_id>/map-data")
def farm_map_data(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    try:
        payload = _build_farm_map_feature_collection(
            farm,
            include_water=True,
            allow_missing_kml=True,
        )
    except ET.ParseError:
        expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
        return (
            jsonify(
                {
                    "error": "Unable to parse farm KML file",
                    "farm_id": str(farm.id),
                    "farm_name": farm.name,
                    "kml_path": str(expected_kml_path),
                }
            ),
            500,
        )

    return jsonify(payload)


@bp.get("/dashboard/map-data")
def dashboard_map_data():
    farms = Farm.query.order_by(Farm.name).all()

    combined_features = []
    unmatched_placemarks = []
    paddocks_without_kml = []
    missing_kml_farms = []
    invalid_kml_farms = []
    kml_paths = []

    for farm in farms:
        expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
        try:
            payload = _build_farm_map_feature_collection(farm)
        except FileNotFoundError:
            missing_kml_farms.append({"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)})
            continue
        except ET.ParseError:
            invalid_kml_farms.append({"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)})
            continue

        kml_paths.append(payload["kml_path"])
        combined_features.extend(payload["features"])
        unmatched_placemarks.extend([f"{farm.name}: {name}" for name in payload["unmatched_placemarks"]])
        paddocks_without_kml.extend([f"{farm.name}: {name}" for name in payload["paddocks_without_kml"]])

    return jsonify(
        {
            "type": "FeatureCollection",
            "metric": "grazing_pressure_ratio",
            "generated_at": f"{datetime.utcnow().isoformat()}Z",
            "farm_count": len(farms),
            "mapped_farm_count": len(kml_paths),
            "kml_paths": kml_paths,
            "missing_kml_farms": missing_kml_farms,
            "invalid_kml_farms": invalid_kml_farms,
            "unmatched_placemarks": unmatched_placemarks,
            "paddocks_without_kml": paddocks_without_kml,
            "features": combined_features,
        }
    )


@bp.post("/farms/<farm_id>/stocking-rate")
def update_farm_stocking_rate_form(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    value_raw = (request.form.get("default_stocking_rate_ha_per_lsu") or "").strip()
    try:
        value = Decimal(value_raw)
        if value <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Farm carrying capacity (ha/LSU) must be greater than 0", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    farm.default_stocking_rate_ha_per_lsu = value
    db.session.commit()
    flash("Farm carrying capacity updated", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/paddocks")
def create_paddock_form(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
    try:
        name = PaddockService.validate_available_name(farm_id, request.form.get("name"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    override_value = None
    if override_raw:
        try:
            override_value = Decimal(override_raw)
            if override_value <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Paddock carrying capacity override (ha/LSU) must be greater than 0", "error")
            return redirect(url_for("web.farm_detail", farm_id=farm_id))

    paddock = Paddock(
        farm_id=farm_id,
        name=name,
        area_ha=request.form.get("area_ha") or 0,
        grazeable_area_ha=request.form.get("grazeable_area_ha") or 0,
        stocking_rate_ha_per_lsu_override=override_value,
    )
    db.session.add(paddock)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("A paddock with this name already exists on this farm", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))
    flash(f"Paddock created for {farm.name}", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/mobs")
def create_mob_form(farm_id):
    Farm.query.get_or_404(farm_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Mob name is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    mob = Mob(farm_id=farm_id, name=name, status="active")
    db.session.add(mob)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Active mob name already exists on this farm", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))
    flash("Mob created", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/rainfall")
def create_rainfall_form(farm_id):
    Farm.query.get_or_404(farm_id)
    recorded_on_raw = (request.form.get("recorded_on") or "").strip()
    mm = request.form.get("mm")
    if not mm:
        flash("Rainfall mm is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    try:
        recorded_on = date.fromisoformat(recorded_on_raw) if recorded_on_raw else date.today()
    except ValueError:
        flash("Recorded on must be a valid date", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    try:
        row = RainfallRecord(farm_id=farm_id, recorded_on=recorded_on, mm=mm, source="manual")
        db.session.add(row)
        db.session.commit()
        flash("Rainfall record added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.get("/mobs/<mob_id>")
def mob_detail(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    selected_event_tag = " ".join((request.args.get("event_tag") or "").strip().lower().split())
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
    has_move_paddocks = any(len(rows) > 0 for rows in move_paddocks_by_farm.values())
    has_transfer_destination_mobs = any(len(rows) > 0 for rows in transfer_mobs_by_farm.values())
    default_move_farm_id = str(mob.farm_id)
    default_transfer_farm_id = str(mob.farm_id)
    active_session = next((session for session in mob.grazing_sessions if session.end_at is None), None)
    current_allocations = []
    allocation_total_pct = Decimal("0")
    if active_session:
        for allocation in sorted(active_session.allocations, key=lambda a: a.paddock.name.lower()):
            pct = Decimal(str(allocation.allocation_fraction)) * Decimal("100")
            allocation_total_pct += pct
            current_allocations.append(
                {
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
            }
        )
    event_tag_options = sorted({tag for row in mob_events_all for tag in row["tags"]})
    mob_events = [
        row
        for row in mob_events_all
        if not selected_event_tag or selected_event_tag in row["tags"]
    ]

    return render_template(
        "mob_detail.html",
        mob=mob,
        move_farms=move_farms,
        move_paddocks_by_farm=move_paddocks_by_farm,
        has_move_paddocks=has_move_paddocks,
        default_move_farm_id=default_move_farm_id,
        transfer_mobs_by_farm=transfer_mobs_by_farm,
        has_transfer_destination_mobs=has_transfer_destination_mobs,
        default_transfer_farm_id=default_transfer_farm_id,
        stock_event_types=StockEventType,
        current_allocations=current_allocations,
        allocation_total_pct=float(allocation_total_pct),
        split_group_options=split_group_options,
        mob_events=mob_events,
        event_tag_options=event_tag_options,
        selected_event_tag=selected_event_tag,
    )


@bp.post("/mobs/<mob_id>/adjust")
def mob_adjust_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    try:
        group_type = StockService.get_or_create_group_type(
            species=(request.form.get("species") or "Cattle").strip(),
            breed=(request.form.get("breed") or "mixed").strip(),
            sex=(request.form.get("sex") or "mixed").strip(),
            age_class=(request.form.get("age_class") or "adult").strip(),
        )
        selected_event_type = StockEventType(request.form.get("event_type") or "count")
        quantity = int(request.form.get("quantity") or 0)

        event_type = selected_event_type
        event_quantity = quantity
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
                flash("Count matches current balance. No stock adjustment posted.", "success")
                return redirect(url_for("web.mob_detail", mob_id=mob_id))
            if delta > 0:
                event_type = StockEventType.adjustment_in
                event_quantity = delta
            else:
                event_type = StockEventType.missing
                event_quantity = -delta

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=group_type.id,
            event_type=event_type,
            quantity=event_quantity,
            note=request.form.get("note"),
        )
        db.session.commit()
        flash("Stock updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/balances/edit")
def mob_edit_balance_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    source_group_type_id = (request.form.get("source_animal_group_type_id") or "").strip()
    if not source_group_type_id:
        flash("Select a balance line to edit", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    source_balance = AnimalGroupBalance.query.filter_by(
        mob_id=mob.id,
        animal_group_type_id=source_group_type_id,
    ).first()
    if not source_balance or int(source_balance.head_count) <= 0:
        flash("Selected balance line is no longer available. Refresh and try again.", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    source_group = source_balance.animal_group_type
    source_head = int(source_balance.head_count)
    head_count_raw = (request.form.get("head_count") or "").strip()
    try:
        target_head = int(head_count_raw)
    except ValueError:
        flash("Head count must be a whole number", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    if target_head <= 0:
        flash("Head count must be greater than 0", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    note_text = " ".join((request.form.get("note") or "").strip().split())

    try:
        target_group = StockService.get_or_create_group_type(
            species=source_group.species,
            breed=source_group.breed,
            sex=(request.form.get("sex") or source_group.sex).strip(),
            age_class=(request.form.get("age_class") or source_group.age_class).strip(),
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
            flash("No changes detected for the selected balance line", "success")
            return redirect(url_for("web.mob_detail", mob_id=mob_id))

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
        flash("Balance line updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/events")
def mob_event_create_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    tags_text = request.form.get("event_tags")
    description = request.form.get("event_description")

    try:
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=description,
            raw_tags=tags_text,
        )
        db.session.commit()
        flash("Mob event recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/rename")
def mob_rename_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Mob name is required", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    if name == mob.name:
        flash("Mob name unchanged", "success")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    mob.name = name
    try:
        db.session.commit()
        flash("Mob name updated", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Active mob name already exists on this farm", "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/move")
def mob_move_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    destination_farm_id = (request.form.get("destination_farm_id") or str(mob.farm_id)).strip()
    paddock_ids = request.form.getlist("paddock_id")
    allocation_pcts = request.form.getlist("allocation_pct")

    try:
        if not destination_farm_id:
            raise ValueError("Destination farm is required")
        if not Farm.query.filter_by(id=destination_farm_id).first():
            raise ValueError("Destination farm is invalid")

        valid_paddock_ids = {
            str(p.id) for p in Paddock.query.filter_by(farm_id=destination_farm_id).all()
        }
        allocations = []
        used_paddocks = set()
        total_pct = Decimal("0")

        for paddock_id_raw, pct_raw in zip(paddock_ids, allocation_pcts):
            paddock_id = (paddock_id_raw or "").strip()
            pct_text = (pct_raw or "").strip()
            if not paddock_id and not pct_text:
                continue
            if not paddock_id:
                raise ValueError("Each allocation row requires a paddock")
            if paddock_id not in valid_paddock_ids:
                raise ValueError("Selected paddock is invalid for the chosen destination farm")
            if paddock_id in used_paddocks:
                raise ValueError("Duplicate paddock rows are not allowed")
            if not pct_text:
                raise ValueError("Each allocation row requires a percentage")

            pct = Decimal(pct_text)
            if pct <= 0:
                raise ValueError("Allocation percentages must be greater than 0")
            if pct > 100:
                raise ValueError("Allocation percentages cannot exceed 100")

            used_paddocks.add(paddock_id)
            total_pct += pct
            fraction = pct / Decimal("100")
            allocations.append(
                {
                    "paddock_id": paddock_id,
                    "allocation_fraction": str(fraction),
                }
            )

        if not allocations:
            raise ValueError("At least one paddock allocation is required")
        if total_pct != Decimal("100"):
            raise ValueError("Allocation percentages must add up to 100")

        MovementService.move_mob(
            mob=mob,
            allocations=allocations,
            destination_farm_id=destination_farm_id,
        )
        db.session.commit()
        flash("Mob moved", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Move failed: active mob name already exists on the destination farm", "error")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/transfer")
def mob_transfer_form(mob_id):
    source = _get_active_mob_or_404(mob_id)
    destination_farm_id = (request.form.get("transfer_destination_farm_id") or str(source.farm_id)).strip()
    destination_mob_id = (request.form.get("transfer_destination_mob_id") or "").strip()
    transfer_group_ids = request.form.getlist("transfer_group_id")
    transfer_quantities = request.form.getlist("transfer_quantity")
    note = (request.form.get("transfer_note") or "").strip() or None

    try:
        if not destination_farm_id:
            raise ValueError("Destination farm is required")
        if not Farm.query.filter_by(id=destination_farm_id).first():
            raise ValueError("Destination farm is invalid")
        if not destination_mob_id:
            raise ValueError("Destination mob is required")

        destination_mob = Mob.query.filter_by(id=destination_mob_id, status="active").first()
        if not destination_mob:
            raise ValueError("Destination mob is invalid")
        if str(destination_mob.farm_id) != destination_farm_id:
            raise ValueError("Destination mob is invalid for the selected farm")

        transfer_totals: dict[str, int] = {}
        for group_id_raw, qty_raw in zip(transfer_group_ids, transfer_quantities):
            group_id = (group_id_raw or "").strip()
            qty_text = (qty_raw or "").strip()

            if not group_id and not qty_text:
                continue
            if not group_id or not qty_text:
                raise ValueError("Each transfer row requires a group and quantity")

            try:
                quantity = int(qty_text)
            except ValueError:
                raise ValueError("Transfer quantities must be whole numbers")
            if quantity <= 0:
                raise ValueError("Transfer quantities must be greater than 0")

            transfer_totals[group_id] = transfer_totals.get(group_id, 0) + quantity

        if not transfer_totals:
            raise ValueError("Add at least one transfer row")

        transfers = [
            {"animal_group_type_id": group_id, "quantity": quantity}
            for group_id, quantity in transfer_totals.items()
        ]
        MovementService.transfer_stock_between_mobs(
            source_mob=source,
            destination_mob=destination_mob,
            transfers=transfers,
            destination_farm_id=destination_farm_id,
            note=note,
        )
        db.session.commit()
        flash(f"Transferred stock to {destination_mob.name}", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Transfer failed due to a concurrent stock update. Please retry.", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/deactivate")
def mob_deactivate_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    total_head_count = sum(int(balance.head_count) for balance in mob.balances)
    if total_head_count > 0:
        flash("Mob cannot be deactivated while it still contains stock", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    mob.status = "archived"
    db.session.commit()
    flash("Mob deactivated", "success")
    return redirect(url_for("web.farm_detail", farm_id=mob.farm_id))


@bp.post("/mobs/<mob_id>/split")
def mob_split_form(mob_id):
    source = _get_active_mob_or_404(mob_id)
    split_names = request.form.getlist("split_mob_name")
    split_group_ids = request.form.getlist("split_group_id")
    split_quantities = request.form.getlist("split_quantity")

    try:
        split_map: dict[str, dict[str, int]] = {}

        for name_raw, group_id_raw, qty_raw in zip(split_names, split_group_ids, split_quantities):
            name = (name_raw or "").strip()
            group_id = (group_id_raw or "").strip()
            qty_text = (qty_raw or "").strip()

            if not name and not group_id and not qty_text:
                continue
            if not name or not group_id or not qty_text:
                raise ValueError("Each split row requires a mob name, group, and quantity")

            try:
                qty = int(qty_text)
            except ValueError:
                raise ValueError("Split quantities must be whole numbers")
            if qty <= 0:
                raise ValueError("Split quantities must be greater than 0")

            split_map.setdefault(name, {})
            split_map[name][group_id] = split_map[name].get(group_id, 0) + qty

        if not split_map:
            raise ValueError("Add at least one split allocation row")

        splits = [
            {
                "name": name,
                "groups": [
                    {"animal_group_type_id": group_id, "quantity": quantity}
                    for group_id, quantity in group_totals.items()
                ],
            }
            for name, group_totals in split_map.items()
        ]

        created = MovementService.split_mob(source_mob=source, splits=splits)
        db.session.commit()
        flash(f"Mob split complete. Created {len(created)} mobs.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Split failed: one or more new mob names already exist on this farm", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/paddocks/<paddock_id>/move-mobs")
def paddock_move_all_mobs_form(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    mob_ids = request.form.getlist("mob_id")
    destination_farm_ids = request.form.getlist("destination_farm_id")
    destination_paddock_ids = request.form.getlist("destination_paddock_id")

    try:
        if not mob_ids:
            raise ValueError("No active mobs on this paddock to move")
        if not (
            len(mob_ids) == len(destination_farm_ids) == len(destination_paddock_ids)
        ):
            raise ValueError("Move request is incomplete. Provide destination farm and paddock for each mob")
        if len(set(mob_ids)) != len(mob_ids):
            raise ValueError("Duplicate mob rows are not allowed")

        active_allocations = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
            )
            .all()
        )
        active_mob_ids = {
            str(allocation.grazing_session.mob_id)
            for allocation in active_allocations
            if allocation.grazing_session.mob.status == "active"
        }
        if set(mob_ids) != active_mob_ids:
            raise ValueError("Active mob list is out of date. Refresh and try again.")

        moved_count = 0
        for mob_id_raw, destination_farm_raw, destination_paddock_raw in zip(
            mob_ids, destination_farm_ids, destination_paddock_ids
        ):
            mob_id = (mob_id_raw or "").strip()
            destination_farm_id = (destination_farm_raw or "").strip()
            destination_paddock_id = (destination_paddock_raw or "").strip()

            if not destination_farm_id:
                raise ValueError("Destination farm is required for each mob")
            if not destination_paddock_id:
                raise ValueError("Destination paddock is required for each mob")
            if destination_paddock_id == str(paddock.id):
                raise ValueError("Destination paddock must be different from the current paddock")
            if mob_id not in active_mob_ids:
                raise ValueError("One or more mobs are no longer active on this paddock")

            mob = Mob.query.filter_by(id=mob_id, status="active").first()
            if not mob:
                raise ValueError("One or more selected mobs are invalid")

            MovementService.move_mob(
                mob=mob,
                allocations=[{"paddock_id": destination_paddock_id, "allocation_fraction": "1.0"}],
                destination_farm_id=destination_farm_id,
            )
            moved_count += 1

        db.session.commit()
        flash(f"Moved {moved_count} mob(s) from {paddock.name}", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Move failed: active mob name already exists on one of the destination farms", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))


@bp.post("/paddocks/<paddock_id>/stocking-rate")
def update_paddock_stocking_rate_form(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
    if not override_raw:
        paddock.stocking_rate_ha_per_lsu_override = None
        db.session.commit()
        flash("Paddock carrying capacity override cleared. Farm default now applies.", "success")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    try:
        override_value = Decimal(override_raw)
        if override_value <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Paddock carrying capacity override (ha/LSU) must be greater than 0", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    paddock.stocking_rate_ha_per_lsu_override = override_value
    db.session.commit()
    flash("Paddock carrying capacity override updated", "success")
    return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))


@bp.post("/paddocks/<paddock_id>/rename")
def rename_paddock_form(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    try:
        result = PaddockService.rename_paddock(
            paddock,
            request.form.get("name"),
            instance_path=current_app.instance_path,
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))
    except IntegrityError:
        db.session.rollback()
        flash("A paddock with this name already exists on this farm", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    if not result["changed"]:
        flash("Paddock name unchanged", "success")
    elif result["map_updated"]:
        flash("Paddock name updated", "success")
    else:
        flash("Paddock name updated. No farm map file was found to update.", "success")
    return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))


@bp.get("/paddocks/<paddock_id>")
def paddock_detail(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    stock_summary = ReportingService.paddock_current_stock_summary(paddock_id)
    farms = Farm.query.order_by(Farm.name).all()
    all_paddocks = Paddock.query.filter_by(status="active").order_by(Paddock.name).all()
    bulk_move_farms = [{"id": str(farm.id), "name": farm.name} for farm in farms]
    bulk_move_paddocks_by_farm = {str(farm.id): [] for farm in farms}
    for option_paddock in all_paddocks:
        bulk_move_paddocks_by_farm.setdefault(str(option_paddock.farm_id), []).append(
            {"id": str(option_paddock.id), "name": option_paddock.name}
        )

    today = date.today()
    try:
        period_start_date = _parse_query_date(request.args.get("period_start")) or date(today.year, 1, 1)
        period_end_date = _parse_query_date(request.args.get("period_end")) or today
    except ValueError:
        flash("Analysis dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    if period_end_date < period_start_date:
        flash("Analysis end date must be on or after the start date", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    period_start_dt = datetime.combine(period_start_date, time.min)
    period_end_dt = datetime.combine(period_end_date + timedelta(days=1), time.min)
    current_dt = datetime.utcnow()
    this_year_start_dt = datetime(today.year, 1, 1)

    grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
    area_ha = float(paddock.area_ha or 0)
    effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha

    farm_stocking_rate = float(paddock.farm.default_stocking_rate_ha_per_lsu)
    paddock_override_rate = (
        float(paddock.stocking_rate_ha_per_lsu_override)
        if paddock.stocking_rate_ha_per_lsu_override is not None
        else None
    )
    effective_stocking_rate = paddock_override_rate or farm_stocking_rate
    grazing_capacity_sdh = (365.0 / effective_stocking_rate) if effective_stocking_rate > 0 else 0.0

    current_lsu = ReportingService.paddock_current_lsu_breakdown(paddock_id)
    current_activity = ReportingService.paddock_continuous_activity(paddock)
    paddock_ha_per_current_lsu = (
        area_ha / current_lsu["total_lsu"] if current_lsu["total_lsu"] > 0 else None
    )
    current_stocking_density = (
        current_lsu["total_lsu"] / effective_area_ha if effective_area_ha > 0 else None
    )

    year_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
        paddock,
        period_start=this_year_start_dt,
        period_end=current_dt,
    )
    sdh_used_this_year = (year_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    sdh_remaining_this_year = (
        grazing_capacity_sdh - sdh_used_this_year if sdh_used_this_year is not None else None
    )

    period_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
        paddock,
        period_start=period_start_dt,
        period_end=period_end_dt,
    )
    period_days = (period_end_dt - period_start_dt).total_seconds() / 86400.0
    period_sdh_used = (period_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    period_avg_stocking_density = (
        (period_lsu_days / (effective_area_ha * period_days))
        if effective_area_ha > 0 and period_days > 0
        else None
    )

    history_allocations = sorted(
        paddock.grazing_allocations,
        key=lambda a: a.grazing_session.start_at,
        reverse=True,
    )
    active_allocations = (
        GrazingAllocation.query.join(GrazingSession)
        .filter(
            GrazingAllocation.paddock_id == paddock_id,
            GrazingSession.end_at.is_(None),
        )
        .all()
    )
    bulk_move_mob_rows = []
    seen_mob_ids = set()
    for allocation in sorted(active_allocations, key=lambda row: row.grazing_session.mob.name.lower()):
        session = allocation.grazing_session
        mob = session.mob
        mob_id = str(mob.id)
        if mob.status != "active" or mob_id in seen_mob_ids:
            continue
        seen_mob_ids.add(mob_id)
        bulk_move_mob_rows.append(
            {
                "mob_id": mob_id,
                "mob_name": mob.name,
                "allocation_pct": float(allocation.allocation_fraction) * 100.0,
                "default_destination_farm_id": str(mob.farm_id),
            }
        )

    history = []
    for allocation in history_allocations:
        session = allocation.grazing_session
        allocated_lsu = ReportingService.allocation_lsu(allocation)
        history.append(
            {
                "allocation": allocation,
                "allocated_lsu": allocated_lsu,
                "mob_name": session.mob.name,
            }
        )
    local_water_assets = WaterNetworkService.local_assets_for_paddock(str(paddock.id))
    serving_troughs = WaterNetworkService.troughs_serving_paddock(str(paddock.id))

    return render_template(
        "paddock_detail.html",
        paddock=paddock,
        stock_summary=stock_summary,
        history=history,
        area_ha=area_ha,
        current_lsu=current_lsu,
        current_activity=current_activity,
        paddock_ha_per_current_lsu=paddock_ha_per_current_lsu,
        effective_stocking_rate=effective_stocking_rate,
        farm_stocking_rate=farm_stocking_rate,
        paddock_override_rate=paddock_override_rate,
        grazing_capacity_sdh=grazing_capacity_sdh,
        sdh_used_this_year=sdh_used_this_year,
        sdh_remaining_this_year=sdh_remaining_this_year,
        current_stocking_density=current_stocking_density,
        period_start=period_start_date,
        period_end=period_end_date,
        period_sdh_used=period_sdh_used,
        period_avg_stocking_density=period_avg_stocking_density,
        period_lsu_days=period_lsu_days,
        effective_area_ha=effective_area_ha,
        bulk_move_farms=bulk_move_farms,
        bulk_move_paddocks_by_farm=bulk_move_paddocks_by_farm,
        bulk_move_mob_rows=bulk_move_mob_rows,
        local_water_assets=local_water_assets,
        serving_troughs=serving_troughs,
        water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
    )
