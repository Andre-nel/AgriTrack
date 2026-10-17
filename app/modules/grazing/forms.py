from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.grazing.constants import (
    GRAZING_ANALYTICS_DEFAULT_METRICS,
    GRAZING_ANALYTICS_METRIC_LABELS,
    GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES,
    GRAZING_ANALYTICS_POINT_FILTER_OPERATORS,
    GRAZING_ANALYTICS_SPLIT_MODES,
)


def parse_query_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def normalize_grazing_metric_selection(raw_values: list[str]) -> list[str]:
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


def normalize_grazing_split_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_SPLIT_MODES:
        return "metric"
    return candidate


def normalize_grazing_point_filter_metric(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_METRIC_LABELS:
        return GRAZING_ANALYTICS_DEFAULT_METRICS[0]
    return candidate


def normalize_grazing_point_filter_operator(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_POINT_FILTER_OPERATORS:
        return "gt"
    return candidate


def normalize_grazing_point_filter_match_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES:
        return "has_any"
    return candidate


@dataclass(frozen=True)
class FarmLsuHistoryFilters:
    farm_ids: tuple[str, ...]
    start_date: date
    end_date: date
    split_mode: str


def parse_farm_lsu_history_filters(args, farms, *, today: date) -> FarmLsuHistoryFilters:
    """Explicit invalid selections must never expand a report to all farms."""
    valid_ids = {str(farm.id) for farm in farms}
    raw_ids = args.getlist("farm_id")
    selected = tuple(dict.fromkeys(value.strip() for value in raw_ids))
    if raw_ids and (not all(selected) or not set(selected).issubset(valid_ids)):
        raise ValueError("Select one or more valid farms. An unknown or empty farm was supplied.")
    selected = selected or tuple(str(farm.id) for farm in farms)
    try:
        end_date = parse_query_date(args.get("end_date")) or today
        start_date = parse_query_date(args.get("start_date")) or (today - timedelta(days=364))
    except ValueError as error:
        raise ValueError("Dates must be valid dates in YYYY-MM-DD format.") from error
    if end_date < start_date:
        raise ValueError("End date must be on or after start date.")
    if end_date > today:
        raise ValueError("End date cannot be in the future; LSU history excludes projections.")
    split_mode = args.get("split_mode", "aggregate")
    if split_mode not in {"aggregate", "farm"}:
        raise ValueError("Split mode must be aggregate or farm.")
    return FarmLsuHistoryFilters(selected, start_date, end_date, split_mode)
