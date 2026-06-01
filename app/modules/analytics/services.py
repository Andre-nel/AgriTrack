from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuBreakdownHistory,
    GrazingSession,
    JournalEntry,
    Paddock,
)
from app.modules.analytics.constants import (
    LSU_PADDOCK_TRACKING_METRIC_AXIS_LABELS,
    LSU_PADDOCK_TRACKING_METRIC_LABELS,
)
from app.services.mob_event_service import MobEventService


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
                "allocated_head_count": float(row.head_count or 0)
                * float(row.allocation_fraction),
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
