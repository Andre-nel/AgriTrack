from datetime import date

from app.models import Paddock
from app.modules.grazing.constants import (
    GRAZING_ANALYTICS_METRIC_AXIS_LABELS,
    GRAZING_ANALYTICS_METRIC_LABELS,
)


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


def filter_grazing_paddocks_by_point_rule(
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


def grazing_paddock_series_label(paddock: Paddock, include_farm_name: bool) -> str:
    if include_farm_name:
        return f"{paddock.farm.name} | {paddock.name}"
    return paddock.name


def flatten_grazing_periods(
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


def build_grazing_chart_panels(
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
                        "label": grazing_paddock_series_label(paddock, include_farm_name),
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
                "group_title": grazing_paddock_series_label(paddock, include_farm_name),
                "metric_panels": metric_panels,
            }
        )
    return panels


def grazing_uncovered_paddocks_in_range(
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
