from datetime import date

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
