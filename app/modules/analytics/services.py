from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuBreakdownHistory,
    GrazingSession,
    JournalEntry,
    Paddock,
    ShearingBale,
    ShearingEntry,
    ShearingSession,
    WaterAsset,
    WaterAssetStateHistory,
)
from app.modules.analytics.constants import (
    LSU_PADDOCK_TRACKING_METRIC_AXIS_LABELS,
    LSU_PADDOCK_TRACKING_METRIC_LABELS,
    WATER_ASSET_STATE_FIELD_LABELS,
)
from app.services.mob_event_service import MobEventService
from app.services.shearing_service import ShearingService
from app.services.water_network_service import WaterNetworkService


def create_journal_entry(
    *,
    farm_id: str,
    tags_raw: str | None,
    description: str | None,
    event_at_raw: str | None,
) -> JournalEntry:
    farm = Farm.query.filter_by(id=(farm_id or "").strip()).first()
    if not farm:
        raise ValueError("Journal entry farm is required")

    try:
        tags = MobEventService.parse_tags(tags_raw)
        description_text = (description or "").strip()
        if not description_text:
            raise ValueError("Description is required")
        if len(description_text) > MobEventService.MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be {MobEventService.MAX_DESCRIPTION_LENGTH} characters or fewer"
            )

        event_at_text = (event_at_raw or "").strip()
        if event_at_text:
            event_at = datetime.fromisoformat(event_at_text)
            if event_at.tzinfo is None:
                event_at = event_at.replace(tzinfo=timezone.utc)
        else:
            event_at = datetime.now(timezone.utc)

        entry = JournalEntry(
            farm_id=farm.id,
            event_at=event_at,
            tags_csv=MobEventService.tags_to_csv(tags),
            description=description_text,
        )
        db.session.add(entry)
        db.session.commit()
        return entry
    except ValueError:
        db.session.rollback()
        raise


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _paddock_label(paddock: Paddock, include_farm_name: bool) -> str:
    if include_farm_name:
        return f"{paddock.farm.name} | {paddock.name}"
    return paddock.name


def _choice_label(value: str | None) -> str:
    if value is None:
        return "N/A"
    return value.replace("_", " ").title()


def _water_asset_label(asset: WaterAsset, include_farm_name: bool) -> str:
    if include_farm_name and asset.farm:
        return f"{asset.farm.name} | {asset.name}"
    return asset.name


def _water_asset_state_from_row(row: WaterAssetStateHistory) -> dict:
    return {
        "active": bool(row.active),
        "status": row.status,
        "water_level": row.water_level,
    }


def _water_asset_history_rows(
    *,
    water_asset_ids: list[str],
    end_date: date,
) -> list[WaterAssetStateHistory]:
    if not water_asset_ids:
        return []

    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    return (
        WaterAssetStateHistory.query.filter(
            WaterAssetStateHistory.water_asset_id.in_(water_asset_ids),
            WaterAssetStateHistory.changed_at < end_dt,
        )
        .order_by(
            WaterAssetStateHistory.water_asset_id.asc(),
            WaterAssetStateHistory.changed_at.asc(),
            WaterAssetStateHistory.id.asc(),
        )
        .all()
    )


def _water_asset_category_maps(
    *,
    field: str,
    assets: list[WaterAsset],
    history_rows: list[WaterAssetStateHistory],
) -> dict:
    if field == "active":
        return {
            "value_to_index": {False: 0, True: 1},
            "index_to_label": {0: "Inactive", 1: "Active"},
        }

    if field == "water_level":
        ordered_values = list(WaterNetworkService.WATER_LEVEL_OPTIONS)
        observed_values = [
            *(asset.water_level for asset in assets),
            *(row.water_level for row in history_rows),
        ]
        discovered_values = {
            value
            for value in observed_values
            if value
        }
        ordered_values.extend(sorted(discovered_values - set(ordered_values)))
    else:
        observed_values = [
            *(asset.status for asset in assets),
            *(row.status for row in history_rows),
        ]
        discovered_values = {
            value
            for value in observed_values
            if value
        }
        ordered_values = sorted(discovered_values)

    value_to_index = {value: index for index, value in enumerate(ordered_values)}
    return {
        "value_to_index": value_to_index,
        "index_to_label": {index: _choice_label(value) for value, index in value_to_index.items()},
    }


def _build_water_asset_daily_states(
    *,
    history_rows: list[WaterAssetStateHistory],
    start_date: date,
    end_date: date,
) -> tuple[list[dict | None], list[WaterAssetStateHistory]]:
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    current_state = None
    in_range_rows = []

    for row in history_rows:
        changed_at = _normalize_datetime(row.changed_at)
        if changed_at is None:
            continue
        if changed_at < start_dt:
            current_state = _water_asset_state_from_row(row)
        elif changed_at < end_dt:
            in_range_rows.append(row)

    daily_states = []
    row_index = 0
    cursor = start_date
    while cursor <= end_date:
        day_end = datetime.combine(cursor + timedelta(days=1), time.min)
        while row_index < len(in_range_rows):
            row = in_range_rows[row_index]
            changed_at = _normalize_datetime(row.changed_at)
            if changed_at is None or changed_at >= day_end:
                break
            current_state = _water_asset_state_from_row(row)
            row_index += 1
        daily_states.append(current_state.copy() if current_state is not None else None)
        cursor += timedelta(days=1)

    return daily_states, in_range_rows


def _dominant_value(
    counts: dict,
    *,
    value_order: list | tuple,
):
    if not counts:
        return None
    order_by_value = {value: index for index, value in enumerate(value_order)}
    return sorted(
        counts,
        key=lambda value: (
            -counts[value],
            order_by_value.get(value, len(order_by_value)),
            str(value),
        ),
    )[0]


def _state_value_counts(daily_states: list[dict | None], field: str) -> dict:
    counts = defaultdict(int)
    for state in daily_states:
        if state is None:
            continue
        value = state.get(field)
        if value is None:
            continue
        counts[value] += 1
    return counts


def _water_asset_change_count(
    rows: list[WaterAssetStateHistory],
    previous_field: str,
    field: str,
) -> int:
    total = 0
    for row in rows:
        if row.change_type != "updated":
            continue
        if getattr(row, previous_field) != getattr(row, field):
            total += 1
    return total


def _water_asset_summary_row(
    *,
    asset: WaterAsset,
    daily_states: list[dict | None],
    in_range_rows: list[WaterAssetStateHistory],
    labels: list[str],
    include_farm_name: bool,
) -> dict:
    known_indexes = [index for index, state in enumerate(daily_states) if state is not None]
    latest_state = daily_states[known_indexes[-1]] if known_indexes else None
    active_counts = _state_value_counts(daily_states, "active")
    status_counts = _state_value_counts(daily_states, "status")
    water_level_counts = _state_value_counts(daily_states, "water_level")
    dominant_status = _dominant_value(status_counts, value_order=sorted(status_counts))
    dominant_water_level = _dominant_value(
        water_level_counts,
        value_order=WaterNetworkService.WATER_LEVEL_OPTIONS,
    )

    latest_active = latest_state.get("active") if latest_state else None
    latest_status = latest_state.get("status") if latest_state else None
    latest_water_level = latest_state.get("water_level") if latest_state else None
    return {
        "asset_name": _water_asset_label(asset, include_farm_name),
        "asset_type_label": WaterNetworkService.ASSET_TYPE_LABELS.get(
            asset.asset_type,
            asset.asset_type,
        ),
        "latest_active_label": (
            "Active" if latest_active is True else "Inactive" if latest_active is False else "N/A"
        ),
        "latest_status_label": _choice_label(latest_status),
        "latest_water_level_label": _choice_label(latest_water_level),
        "first_observed_date": labels[known_indexes[0]] if known_indexes else None,
        "last_observed_date": labels[known_indexes[-1]] if known_indexes else None,
        "history_event_count": len(in_range_rows),
        "active_days": active_counts.get(True, 0),
        "inactive_days": active_counts.get(False, 0),
        "status_change_count": _water_asset_change_count(
            in_range_rows,
            "previous_status",
            "status",
        ),
        "water_level_change_count": _water_asset_change_count(
            in_range_rows,
            "previous_water_level",
            "water_level",
        ),
        "dominant_status_label": _choice_label(dominant_status),
        "dominant_water_level_label": _choice_label(dominant_water_level),
        "empty_low_days": sum(
            count for value, count in water_level_counts.items() if value in {"empty", "low"}
        ),
        "half_or_better_days": sum(
            count
            for value, count in water_level_counts.items()
            if value in {"half", "high", "full"}
        ),
    }


def _water_asset_chart_values(
    *,
    daily_states: list[dict | None],
    field: str,
    value_to_index: dict,
) -> list[int | None]:
    values = []
    for state in daily_states:
        if state is None:
            values.append(None)
            continue
        value = state.get(field)
        if value is None:
            values.append(None)
            continue
        if field == "active":
            values.append(1 if bool(value) else 0)
        else:
            values.append(value_to_index.get(value))
    return values


def _water_asset_chart_panel(
    *,
    field: str,
    title: str,
    datasets: list[dict],
    index_to_label: dict,
) -> dict:
    return {
        "id": f"{field}:{title}",
        "title": title,
        "field": field,
        "y_axis_label": WATER_ASSET_STATE_FIELD_LABELS[field],
        "y_min": 0,
        "y_max": max(index_to_label) if index_to_label else 0,
        "value_labels": {str(index): label for index, label in index_to_label.items()},
        "datasets": datasets,
    }


def build_water_asset_state_report(
    *,
    assets: list[WaterAsset],
    start_date: date,
    end_date: date,
    state_fields: list[str],
    plot_mode: str,
    include_farm_name: bool,
) -> dict:
    labels = []
    cursor = start_date
    while cursor <= end_date:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)

    if not assets:
        return {
            "chart_payload": {"labels": labels, "plot_mode": plot_mode, "panels": []},
            "summary_rows": [],
            "has_history": False,
        }

    water_asset_ids = [str(asset.id) for asset in assets]
    history_rows = _water_asset_history_rows(
        water_asset_ids=water_asset_ids,
        end_date=end_date,
    )
    rows_by_asset_id: dict[str, list[WaterAssetStateHistory]] = defaultdict(list)
    for row in history_rows:
        rows_by_asset_id[str(row.water_asset_id)].append(row)

    category_maps = {
        field: _water_asset_category_maps(field=field, assets=assets, history_rows=history_rows)
        for field in state_fields
    }
    state_series_by_asset_id = {}
    summary_rows = []
    for asset in assets:
        asset_id = str(asset.id)
        daily_states, in_range_rows = _build_water_asset_daily_states(
            history_rows=rows_by_asset_id.get(asset_id, []),
            start_date=start_date,
            end_date=end_date,
        )
        state_series_by_asset_id[asset_id] = {
            "asset": asset,
            "daily_states": daily_states,
        }
        summary_rows.append(
            _water_asset_summary_row(
                asset=asset,
                daily_states=daily_states,
                in_range_rows=in_range_rows,
                labels=labels,
                include_farm_name=include_farm_name,
            )
        )

    panels = []
    if plot_mode == "asset":
        for asset in assets:
            asset_id = str(asset.id)
            asset_label = _water_asset_label(asset, include_farm_name)
            daily_states = state_series_by_asset_id[asset_id]["daily_states"]
            for field in state_fields:
                category_map = category_maps[field]
                values = _water_asset_chart_values(
                    daily_states=daily_states,
                    field=field,
                    value_to_index=category_map["value_to_index"],
                )
                if not any(value is not None for value in values):
                    continue
                panels.append(
                    _water_asset_chart_panel(
                        field=field,
                        title=f"{asset_label} | {WATER_ASSET_STATE_FIELD_LABELS[field]}",
                        datasets=[{"label": asset_label, "values": values}],
                        index_to_label=category_map["index_to_label"],
                    )
                )
    else:
        for field in state_fields:
            category_map = category_maps[field]
            datasets = []
            for asset in assets:
                asset_id = str(asset.id)
                values = _water_asset_chart_values(
                    daily_states=state_series_by_asset_id[asset_id]["daily_states"],
                    field=field,
                    value_to_index=category_map["value_to_index"],
                )
                if not any(value is not None for value in values):
                    continue
                datasets.append(
                    {
                        "label": _water_asset_label(asset, include_farm_name),
                        "values": values,
                    }
                )
            if datasets:
                panels.append(
                    _water_asset_chart_panel(
                        field=field,
                        title=f"{WATER_ASSET_STATE_FIELD_LABELS[field]} Over Time",
                        datasets=datasets,
                        index_to_label=category_map["index_to_label"],
                    )
                )

    summary_rows.sort(key=lambda row: (row["asset_name"].lower(), row["asset_type_label"].lower()))
    return {
        "chart_payload": {
            "labels": labels,
            "plot_mode": plot_mode,
            "panels": panels,
        },
        "summary_rows": summary_rows,
        "has_history": bool(history_rows),
    }


def _build_lsu_day_stats(*, rows: list[dict], day: date, area_ha: float) -> dict:
    day_start = datetime.combine(day, time.min)
    day_end = day_start + timedelta(days=1)
    current_lsu = 0.0
    current_head_count = 0.0
    avg_lsu = 0.0
    avg_head_count = 0.0
    lsu_event_deltas: dict[datetime, float] = defaultdict(float)
    head_event_deltas: dict[datetime, float] = defaultdict(float)

    for row in rows:
        row_start = row["start"]
        row_end = row["end"] or day_end
        if row_start < day_start and row_end > day_start:
            current_lsu += row["allocated_lsu"]
            current_head_count += row["allocated_head_count"]

        overlap_start = max(row_start, day_start)
        overlap_end = min(row_end, day_end)
        if overlap_end <= overlap_start:
            continue

        duration_days = (overlap_end - overlap_start).total_seconds() / 86400.0
        avg_lsu += row["allocated_lsu"] * duration_days
        avg_head_count += row["allocated_head_count"] * duration_days

        if row_start >= day_start and row_start < day_end:
            lsu_event_deltas[row_start] += row["allocated_lsu"]
            head_event_deltas[row_start] += row["allocated_head_count"]
        if row["end"] is not None and row_end > day_start and row_end < day_end:
            lsu_event_deltas[row_end] -= row["allocated_lsu"]
            head_event_deltas[row_end] -= row["allocated_head_count"]

    peak_lsu = current_lsu
    peak_head_count = current_head_count
    for point in sorted(set(lsu_event_deltas) | set(head_event_deltas)):
        current_lsu += lsu_event_deltas[point]
        current_head_count += head_event_deltas[point]
        peak_lsu = max(peak_lsu, current_lsu)
        peak_head_count = max(peak_head_count, current_head_count)

    end_of_day_lsu = current_lsu
    end_of_day_head_count = current_head_count
    lsu_per_ha = (end_of_day_lsu / area_ha) if area_ha > 0 else None
    peak_lsu_per_ha = (peak_lsu / area_ha) if area_ha > 0 else None
    return {
        "date": day.isoformat(),
        "current_lsu": round(end_of_day_lsu, 4),
        "head_count": round(end_of_day_head_count, 4),
        "avg_lsu": round(avg_lsu, 4),
        "avg_head_count": round(avg_head_count, 4),
        "peak_lsu": round(peak_lsu, 4),
        "peak_head_count": round(peak_head_count, 4),
        "lsu_per_ha": round(lsu_per_ha, 4) if lsu_per_ha is not None else None,
        "peak_lsu_per_ha": round(peak_lsu_per_ha, 4) if peak_lsu_per_ha is not None else None,
    }


def _lsu_history_rows(
    *,
    paddock_ids: list[str],
    start_date: date,
    end_date: date,
    species: str,
) -> list[GrazingAllocationLsuBreakdownHistory]:
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    start_dt = datetime.combine(start_date, time.min)
    query = (
        GrazingAllocationLsuBreakdownHistory.query.join(AnimalGroupType)
        .filter(
            GrazingAllocationLsuBreakdownHistory.paddock_id.in_(paddock_ids),
            GrazingAllocationLsuBreakdownHistory.effective_from < end_dt,
            or_(
                GrazingAllocationLsuBreakdownHistory.effective_to.is_(None),
                GrazingAllocationLsuBreakdownHistory.effective_to > start_dt,
            ),
        )
        .order_by(
            GrazingAllocationLsuBreakdownHistory.paddock_id.asc(),
            AnimalGroupType.species.asc(),
            GrazingAllocationLsuBreakdownHistory.effective_from.asc(),
            GrazingAllocationLsuBreakdownHistory.id.asc(),
        )
    )
    if species:
        query = query.filter(AnimalGroupType.species == species)
    return query.all()


def earliest_lsu_paddock_breakdown_date(
    *,
    paddock_ids: list[str],
    species: str,
) -> date | None:
    if not paddock_ids:
        return None
    query = db.session.query(func.min(GrazingAllocationLsuBreakdownHistory.effective_from)).filter(
        GrazingAllocationLsuBreakdownHistory.paddock_id.in_(paddock_ids)
    )
    if species:
        query = query.join(AnimalGroupType).filter(AnimalGroupType.species == species)

    earliest = _normalize_datetime(query.scalar())
    return earliest.date() if earliest else None


def lsu_paddock_breakdown_uncovered_paddocks(
    *,
    paddocks: list[Paddock],
    start_date: date,
    species: str,
    include_farm_name: bool,
) -> list[str]:
    if not paddocks:
        return []

    paddock_ids = [str(paddock.id) for paddock in paddocks]
    session_bounds = (
        db.session.query(
            GrazingAllocation.paddock_id.label("paddock_id"),
            func.min(GrazingSession.start_at).label("earliest_session"),
        )
        .join(GrazingSession, GrazingAllocation.grazing_session_id == GrazingSession.id)
        .filter(GrazingAllocation.paddock_id.in_(paddock_ids))
        .group_by(GrazingAllocation.paddock_id)
        .all()
    )
    earliest_session_by_paddock = {
        str(row.paddock_id): _normalize_datetime(row.earliest_session) for row in session_bounds
    }

    history_query = (
        db.session.query(
            GrazingAllocationLsuBreakdownHistory.paddock_id.label("paddock_id"),
            func.min(GrazingAllocationLsuBreakdownHistory.effective_from).label(
                "earliest_history"
            ),
        )
        .filter(GrazingAllocationLsuBreakdownHistory.paddock_id.in_(paddock_ids))
        .group_by(GrazingAllocationLsuBreakdownHistory.paddock_id)
    )
    if species:
        history_query = history_query.join(AnimalGroupType).filter(AnimalGroupType.species == species)

    earliest_history_by_paddock = {
        str(row.paddock_id): _normalize_datetime(row.earliest_history)
        for row in history_query.all()
    }

    uncovered = []
    for paddock in paddocks:
        paddock_id = str(paddock.id)
        earliest_session = earliest_session_by_paddock.get(paddock_id)
        earliest_history = earliest_history_by_paddock.get(paddock_id)
        if earliest_session is None:
            continue
        if earliest_history is None:
            uncovered.append(_paddock_label(paddock, include_farm_name))
            continue
        if start_date < earliest_history.date() and earliest_session < earliest_history:
            uncovered.append(_paddock_label(paddock, include_farm_name))
    return uncovered


def build_lsu_paddock_tracking_report(
    *,
    paddocks: list[Paddock],
    start_date: date,
    end_date: date,
    species: str,
    metric: str,
    plot_mode: str,
    group_by_species: bool,
    include_farm_name: bool,
    min_value: float | None = None,
) -> dict:
    labels = []
    cursor = start_date
    while cursor <= end_date:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)

    if not paddocks:
        return {
            "chart_payload": {"labels": labels, "plot_mode": plot_mode, "panels": []},
            "summary_rows": [],
            "has_history": False,
        }

    paddock_by_id = {str(paddock.id): paddock for paddock in paddocks}
    paddock_ids = list(paddock_by_id)
    history_rows = _lsu_history_rows(
        paddock_ids=paddock_ids,
        start_date=start_date,
        end_date=end_date,
        species=species,
    )
    rows_by_series: dict[tuple[str, str | None], list[dict]] = defaultdict(list)
    species_seen_by_paddock: dict[str, set[str]] = defaultdict(set)

    for row in history_rows:
        paddock_id = str(row.paddock_id)
        row_species = row.animal_group_type.species if row.animal_group_type else "Unknown"
        species_key = row_species if group_by_species else None
        species_seen_by_paddock[paddock_id].add(row_species)
        rows_by_series[(paddock_id, species_key)].append(
            {
                "start": _normalize_datetime(row.effective_from),
                "end": _normalize_datetime(row.effective_to),
                "allocated_lsu": float(row.allocated_lsu),
                "allocated_head_count": float(row.allocated_head_count),
            }
        )

    series_keys = []
    if group_by_species:
        for paddock in paddocks:
            paddock_id = str(paddock.id)
            if species:
                series_keys.append((paddock_id, species))
                continue
            for row_species in sorted(species_seen_by_paddock.get(paddock_id, set())):
                series_keys.append((paddock_id, row_species))
    else:
        series_keys = [(str(paddock.id), None) for paddock in paddocks]

    series_payloads = []
    summary_rows = []
    total_days = len(labels)
    for paddock_id, species_key in series_keys:
        paddock = paddock_by_id[paddock_id]
        area_ha = float(paddock.area_ha or 0)
        daily_rows = []
        cursor = start_date
        series_rows = rows_by_series.get((paddock_id, species_key), [])
        while cursor <= end_date:
            daily_rows.append(
                _build_lsu_day_stats(rows=series_rows, day=cursor, area_ha=area_ha)
            )
            cursor += timedelta(days=1)

        values = [row[metric] for row in daily_rows]
        if min_value is not None and not any(
            value is not None and float(value) >= min_value for value in values
        ):
            continue

        paddock_label = _paddock_label(paddock, include_farm_name)
        display_species = species_key or species or "All selected species"
        series_label = (
            f"{paddock_label} | {display_species}" if group_by_species else paddock_label
        )
        total_lsu_days = sum(row["avg_lsu"] for row in daily_rows)
        grazing_days = sum(1 for row in daily_rows if row["avg_lsu"] > 0)
        last_grazed_dates = [row["date"] for row in daily_rows if row["avg_lsu"] > 0]
        peak_lsu_per_ha_values = [
            row["peak_lsu_per_ha"] for row in daily_rows if row["peak_lsu_per_ha"] is not None
        ]
        average_lsu = total_lsu_days / total_days if total_days else 0.0
        lsu_days_per_ha = total_lsu_days / area_ha if area_ha > 0 else None
        average_lsu_per_ha = average_lsu / area_ha if area_ha > 0 else None

        series_payloads.append(
            {
                "key": f"{paddock_id}:{species_key or 'all'}",
                "paddock_id": paddock_id,
                "label": series_label,
                "values": values,
            }
        )
        summary_rows.append(
            {
                "paddock_name": paddock_label,
                "species": display_species,
                "total_lsu_days": round(total_lsu_days, 2),
                "lsu_days_per_ha": round(lsu_days_per_ha, 2)
                if lsu_days_per_ha is not None
                else None,
                "average_lsu": round(average_lsu, 2),
                "average_lsu_per_ha": round(average_lsu_per_ha, 4)
                if average_lsu_per_ha is not None
                else None,
                "peak_lsu_per_ha": round(max(peak_lsu_per_ha_values), 4)
                if peak_lsu_per_ha_values
                else None,
                "grazing_days": grazing_days,
                "rest_days": max(0, total_days - grazing_days),
                "last_grazed_date": max(last_grazed_dates) if last_grazed_dates else None,
            }
        )

    y_axis_label = LSU_PADDOCK_TRACKING_METRIC_AXIS_LABELS[metric]
    metric_label = LSU_PADDOCK_TRACKING_METRIC_LABELS[metric]
    if plot_mode == "paddock":
        panels = []
        for paddock in paddocks:
            paddock_id = str(paddock.id)
            datasets = [row for row in series_payloads if row["paddock_id"] == paddock_id]
            if not datasets:
                continue
            panels.append(
                {
                    "id": f"paddock-{paddock_id}",
                    "title": _paddock_label(paddock, include_farm_name),
                    "metric": metric,
                    "y_axis_label": y_axis_label,
                    "datasets": datasets,
                }
            )
    else:
        panels = []
        if series_payloads:
            panels.append(
                {
                    "id": f"overlay-{metric}",
                    "title": f"{metric_label} Over Time",
                    "metric": metric,
                    "y_axis_label": y_axis_label,
                    "datasets": series_payloads,
                }
            )

    summary_rows.sort(key=lambda row: (row["paddock_name"].lower(), row["species"].lower()))
    return {
        "chart_payload": {
            "labels": labels,
            "plot_mode": plot_mode,
            "metric": metric,
            "panels": panels,
        },
        "summary_rows": summary_rows,
        "has_history": bool(history_rows),
    }


SHEARING_MONEY_QUANT = Decimal("0.01")
SHEARING_RATE_QUANT = Decimal("0.0001")
SHEARING_WEIGHT_QUANT = Decimal("0.001")


def _shearing_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _shearing_float(value: Decimal, quant: Decimal) -> float:
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def _shearing_average(
    numerator: Decimal,
    denominator: Decimal,
    quant: Decimal,
) -> float | None:
    if denominator <= 0:
        return None
    return _shearing_float(numerator / denominator, quant)


def _empty_shearing_bale_rollup() -> dict:
    return {
        "total_bales": 0,
        "total_kg": Decimal("0.000"),
        "priced_bales": 0,
        "priced_kg": Decimal("0.000"),
        "total_price": Decimal("0.00"),
    }


def _add_shearing_bale_to_rollup(rollup: dict, bale: ShearingBale) -> None:
    weight = _shearing_decimal(bale.weight_kg)
    rollup["total_bales"] += 1
    rollup["total_kg"] += weight
    if bale.total_price is not None:
        rollup["priced_bales"] += 1
        rollup["priced_kg"] += weight
        rollup["total_price"] += _shearing_decimal(bale.total_price)


def _merge_shearing_bale_rollups(target: dict, source: dict) -> None:
    target["total_bales"] += source["total_bales"]
    target["total_kg"] += source["total_kg"]
    target["priced_bales"] += source["priced_bales"]
    target["priced_kg"] += source["priced_kg"]
    target["total_price"] += source["total_price"]


def _shearing_bale_label(bale: ShearingBale) -> str:
    return bale.bale_code.code if bale.bale_code else bale.code_text


def _shearing_bale_key(bale: ShearingBale) -> str:
    if bale.bale_code_id:
        return str(bale.bale_code_id)
    return f"ad-hoc:{bale.code_key}"


def _filtered_shearing_bales(
    session: ShearingSession,
    selected_bale_code_ids: set[str],
) -> list[ShearingBale]:
    bales = ShearingService.sorted_bales(session.bales)
    if not selected_bale_code_ids:
        return bales
    return [
        bale
        for bale in bales
        if bale.bale_code_id and str(bale.bale_code_id) in selected_bale_code_ids
    ]


def earliest_shearing_session_date(
    *,
    farm_ids: list[str] | None = None,
    species: str = "",
) -> date | None:
    query = db.session.query(func.min(ShearingSession.start_date))
    if farm_ids:
        query = query.filter(ShearingSession.farm_id.in_(farm_ids))
    if species:
        query = query.filter(ShearingSession.species == species)
    return query.scalar()


def latest_shearing_session_date(
    *,
    farm_ids: list[str] | None = None,
    species: str = "",
) -> date | None:
    query = db.session.query(func.max(ShearingSession.start_date))
    if farm_ids:
        query = query.filter(ShearingSession.farm_id.in_(farm_ids))
    if species:
        query = query.filter(ShearingSession.species == species)
    return query.scalar()


def build_shearing_analytics_report(
    *,
    farm_ids: list[str],
    species: str,
    start_date: date,
    end_date: date,
    bale_code_ids: list[str],
    group_by_farm: bool,
) -> dict:
    selected_bale_code_ids = {str(value).strip() for value in bale_code_ids if str(value).strip()}
    query = (
        ShearingSession.query.options(
            selectinload(ShearingSession.farm),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.shearer),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.animal_group_type),
            selectinload(ShearingSession.bales).selectinload(ShearingBale.bale_code),
        )
        .join(Farm, ShearingSession.farm_id == Farm.id)
        .filter(
            ShearingSession.start_date >= start_date,
            ShearingSession.start_date <= end_date,
        )
    )
    if farm_ids:
        query = query.filter(ShearingSession.farm_id.in_(farm_ids))
    if species:
        query = query.filter(ShearingSession.species == species)

    sessions = (
        query.order_by(
            ShearingSession.start_date.asc(),
            Farm.name.asc(),
            ShearingSession.name.asc(),
            ShearingSession.id.asc(),
        )
        .all()
    )

    session_rows = []
    code_rows = []
    code_time_rollups: dict[tuple[str, str], dict] = defaultdict(_empty_shearing_bale_rollup)
    total_kg = Decimal("0.000")
    total_priced_kg = Decimal("0.000")
    total_money_in = Decimal("0.00")
    total_shearing_cost = Decimal("0.00")
    total_animals_shorn = 0

    include_farm_in_session_label = group_by_farm or len({str(session.farm_id) for session in sessions}) > 1

    for session in sessions:
        farm_name = session.farm.name if session.farm else "Unknown Farm"
        session_date = session.start_date.isoformat()
        payout_breakdown = ShearingService.session_breakdown(session)
        animals_shorn = int(payout_breakdown["totals"]["quantity"])
        shearing_cost = _shearing_decimal(payout_breakdown["totals"]["amount"])
        bales = _filtered_shearing_bales(session, selected_bale_code_ids)
        session_rollup = _empty_shearing_bale_rollup()
        code_rollups: dict[str, dict] = {}

        for bale in bales:
            _add_shearing_bale_to_rollup(session_rollup, bale)
            code_key = _shearing_bale_key(bale)
            code_rollup = code_rollups.setdefault(
                code_key,
                {
                    **_empty_shearing_bale_rollup(),
                    "bale_code_id": str(bale.bale_code_id) if bale.bale_code_id else None,
                    "code": _shearing_bale_label(bale),
                },
            )
            _add_shearing_bale_to_rollup(code_rollup, bale)

        total_kg += session_rollup["total_kg"]
        total_priced_kg += session_rollup["priced_kg"]
        total_money_in += session_rollup["total_price"]
        total_shearing_cost += shearing_cost
        total_animals_shorn += animals_shorn

        animals_decimal = Decimal(animals_shorn)
        session_label_parts = []
        if include_farm_in_session_label:
            session_label_parts.append(farm_name)
        session_label_parts.extend([session.name, session_date])
        session_rows.append(
            {
                "farm_id": str(session.farm_id),
                "farm_name": farm_name,
                "session_id": str(session.id),
                "session_name": session.name,
                "species": session.species,
                "session_date": session_date,
                "chart_label": " | ".join(session_label_parts),
                "total_bales": session_rollup["total_bales"],
                "total_kg": _shearing_float(session_rollup["total_kg"], SHEARING_WEIGHT_QUANT),
                "priced_kg": _shearing_float(session_rollup["priced_kg"], SHEARING_WEIGHT_QUANT),
                "total_money_in": _shearing_float(
                    session_rollup["total_price"],
                    SHEARING_MONEY_QUANT,
                ),
                "average_price_per_kg": _shearing_average(
                    session_rollup["total_price"],
                    session_rollup["priced_kg"],
                    SHEARING_RATE_QUANT,
                ),
                "animals_shorn": animals_shorn,
                "average_kg_per_animal": _shearing_average(
                    session_rollup["total_kg"],
                    animals_decimal,
                    SHEARING_WEIGHT_QUANT,
                ),
                "average_money_per_animal": _shearing_average(
                    session_rollup["total_price"],
                    animals_decimal,
                    SHEARING_MONEY_QUANT,
                ),
                "shearing_cost": _shearing_float(shearing_cost, SHEARING_MONEY_QUANT),
                "cost_per_animal": _shearing_average(
                    shearing_cost,
                    animals_decimal,
                    SHEARING_MONEY_QUANT,
                ),
            }
        )

        for code_rollup in code_rollups.values():
            code_label = code_rollup["code"]
            series_label = f"{farm_name} | {code_label}" if group_by_farm else code_label
            _merge_shearing_bale_rollups(
                code_time_rollups[(session_date, series_label)],
                code_rollup,
            )
            code_rows.append(
                {
                    "farm_id": str(session.farm_id),
                    "farm_name": farm_name,
                    "session_id": str(session.id),
                    "session_name": session.name,
                    "species": session.species,
                    "session_date": session_date,
                    "bale_code_id": code_rollup["bale_code_id"],
                    "code": code_label,
                    "bale_count": code_rollup["total_bales"],
                    "kg": _shearing_float(code_rollup["total_kg"], SHEARING_WEIGHT_QUANT),
                    "priced_kg": _shearing_float(code_rollup["priced_kg"], SHEARING_WEIGHT_QUANT),
                    "average_price_per_kg": _shearing_average(
                        code_rollup["total_price"],
                        code_rollup["priced_kg"],
                        SHEARING_RATE_QUANT,
                    ),
                    "total_money_in": _shearing_float(
                        code_rollup["total_price"],
                        SHEARING_MONEY_QUANT,
                    ),
                }
            )

    code_rows.sort(
        key=lambda row: (
            row["session_date"],
            row["farm_name"].lower(),
            row["session_name"].lower(),
            row["code"].lower(),
        )
    )
    date_labels = sorted({date_label for date_label, _series in code_time_rollups})
    code_series_labels = sorted(
        {series for _date_label, series in code_time_rollups},
        key=str.lower,
    )

    def code_dataset(metric: str, series_label: str) -> dict | None:
        values = []
        for date_label in date_labels:
            rollup = code_time_rollups.get((date_label, series_label))
            if rollup is None:
                values.append(None if metric == "price" else 0)
            elif metric == "kg":
                values.append(_shearing_float(rollup["total_kg"], SHEARING_WEIGHT_QUANT))
            elif metric == "money":
                values.append(_shearing_float(rollup["total_price"], SHEARING_MONEY_QUANT))
            else:
                values.append(
                    _shearing_average(
                        rollup["total_price"],
                        rollup["priced_kg"],
                        SHEARING_RATE_QUANT,
                    )
                )

        has_values = any(value is not None and value != 0 for value in values)
        if metric == "price":
            has_values = any(value is not None for value in values)
        if not has_values:
            return None
        return {"label": series_label, "values": values}

    panels = []
    code_panel_specs = [
        ("code-weight", "Weight by Code Over Time", "kg", "kg", "line"),
        ("code-price", "Price per Kg by Code Over Time", "price", "R/kg", "line"),
        ("code-money", "Money In by Code Over Time", "money", "R", "bar"),
    ]
    for panel_id, title, metric, axis_label, chart_type in code_panel_specs:
        datasets = [
            dataset
            for series_label in code_series_labels
            if (dataset := code_dataset(metric, series_label)) is not None
        ]
        if datasets:
            panels.append(
                {
                    "id": panel_id,
                    "title": title,
                    "chart_type": chart_type,
                    "value_format": metric,
                    "labels": date_labels,
                    "y_axis_label": axis_label,
                    "datasets": datasets,
                }
            )

    if session_rows:
        session_labels = [row["chart_label"] for row in session_rows]
        panels.extend(
            [
                {
                    "id": "session-total-kg",
                    "title": "Total Weight Per Session",
                    "chart_type": "bar",
                    "value_format": "kg",
                    "labels": session_labels,
                    "y_axis_label": "kg",
                    "datasets": [
                        {
                            "label": "Total kg",
                            "values": [row["total_kg"] for row in session_rows],
                        }
                    ],
                },
                {
                    "id": "session-money-cost",
                    "title": "Money In And Shearing Costs Per Session",
                    "chart_type": "bar",
                    "value_format": "money",
                    "labels": session_labels,
                    "y_axis_label": "R",
                    "datasets": [
                        {
                            "label": "Total money in",
                            "values": [row["total_money_in"] for row in session_rows],
                        },
                        {
                            "label": "Shearing costs",
                            "values": [row["shearing_cost"] for row in session_rows],
                        },
                    ],
                },
            ]
        )

    total_animals_decimal = Decimal(total_animals_shorn)
    summary = {
        "total_sessions": len(session_rows),
        "total_kg": _shearing_float(total_kg, SHEARING_WEIGHT_QUANT),
        "priced_kg": _shearing_float(total_priced_kg, SHEARING_WEIGHT_QUANT),
        "total_money_in": _shearing_float(total_money_in, SHEARING_MONEY_QUANT),
        "average_price_per_kg": _shearing_average(
            total_money_in,
            total_priced_kg,
            SHEARING_RATE_QUANT,
        ),
        "animals_shorn": total_animals_shorn,
        "average_kg_per_animal": _shearing_average(
            total_kg,
            total_animals_decimal,
            SHEARING_WEIGHT_QUANT,
        ),
        "average_money_per_animal": _shearing_average(
            total_money_in,
            total_animals_decimal,
            SHEARING_MONEY_QUANT,
        ),
        "shearing_cost": _shearing_float(total_shearing_cost, SHEARING_MONEY_QUANT),
        "cost_per_animal": _shearing_average(
            total_shearing_cost,
            total_animals_decimal,
            SHEARING_MONEY_QUANT,
        ),
    }

    return {
        "chart_payload": {
            "labels": date_labels,
            "group_by_farm": group_by_farm,
            "panels": panels,
        },
        "summary": summary,
        "session_rows": session_rows,
        "code_rows": code_rows,
        "has_sessions": bool(session_rows),
    }
