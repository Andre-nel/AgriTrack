from datetime import date
from typing import Mapping

from app.modules.analytics.constants import (
    ANALYTICS_DEFAULT_GROUP_BY,
    ANALYTICS_GROUP_LABELS,
    LSU_PADDOCK_TRACKING_DEFAULT_METRIC,
    LSU_PADDOCK_TRACKING_METRIC_LABELS,
    LSU_PADDOCK_TRACKING_PLOT_MODES,
    WATER_ASSET_ANALYTICS_PLOT_MODES,
    WATER_ASSET_CURRENT_ACTIVE_FILTERS,
    WATER_ASSET_STATE_DEFAULT_FIELDS,
    WATER_ASSET_STATE_FIELD_LABELS,
)
from app.models.water import WATER_ASSET_TYPES


def parse_query_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def normalize_analytics_group_by(raw_values: list[str]) -> list[str]:
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


def normalize_lsu_paddock_tracking_metric(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in LSU_PADDOCK_TRACKING_METRIC_LABELS:
        return LSU_PADDOCK_TRACKING_DEFAULT_METRIC
    return candidate


def normalize_lsu_paddock_tracking_plot_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in LSU_PADDOCK_TRACKING_PLOT_MODES:
        return "overlay"
    return candidate


def normalize_shearing_analytics_species(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate == "sheep":
        return "Sheep"
    if candidate == "goat":
        return "Goat"
    return ""


def normalize_repeated_query_ids(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip()
        if not value or value in seen:
            continue
        selected.append(value)
        seen.add(value)
    return selected


def normalize_water_asset_analytics_asset_types(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in WATER_ASSET_TYPES or value in seen:
            continue
        selected.append(value)
        seen.add(value)
    return selected


def normalize_water_asset_current_active_filter(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in WATER_ASSET_CURRENT_ACTIVE_FILTERS:
        return "all"
    return candidate


def normalize_water_asset_state_fields(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in WATER_ASSET_STATE_FIELD_LABELS or value in seen:
            continue
        selected.append(value)
        seen.add(value)

    if not selected:
        return list(WATER_ASSET_STATE_DEFAULT_FIELDS)
    return selected


def normalize_water_asset_analytics_plot_mode(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate not in WATER_ASSET_ANALYTICS_PLOT_MODES:
        return "overlay"
    return candidate


def normalize_journal_tag(value: str | None) -> str:
    return " ".join((value or "").replace("_", " ").strip().lower().split())


def normalize_journal_tags(values: list[str]) -> list[str]:
    tags = []
    seen = set()
    for value in values:
        tag = normalize_journal_tag(value)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def journal_return_query_args(form: Mapping[str, str]) -> dict[str, str]:
    redirect_kwargs = {}
    fields = {
        "return_farm_id": "farm_id",
        "return_tag": "tag",
        "return_start_date": "start_date",
        "return_end_date": "end_date",
    }
    for source, target in fields.items():
        value = (form.get(source) or "").strip()
        if value:
            redirect_kwargs[target] = value
    return redirect_kwargs
