from types import SimpleNamespace

from app.modules.grazing.presenters import (
    build_grazing_chart_panels,
    filter_grazing_paddocks_by_point_rule,
    flatten_grazing_periods,
)


def _paddock(paddock_id: str, name: str, farm_id: str = "farm-1"):
    return SimpleNamespace(
        id=paddock_id,
        name=name,
        farm_id=farm_id,
        farm=SimpleNamespace(name=f"Farm {farm_id}"),
    )


def test_filter_grazing_paddocks_by_point_rule_supports_has_any_and_has_none():
    north = _paddock("north", "North")
    south = _paddock("south", "South")
    payload = {
        "paddocks": {
            "north": {"metrics": {"current_lsu": [0, 4]}},
            "south": {"metrics": {"current_lsu": [0, 1]}},
        }
    }

    assert filter_grazing_paddocks_by_point_rule(
        [north, south],
        payload,
        metric="current_lsu",
        operator="gt",
        threshold_value=2,
        match_mode="has_any",
    ) == [north]
    assert filter_grazing_paddocks_by_point_rule(
        [north, south],
        payload,
        metric="current_lsu",
        operator="gt",
        threshold_value=2,
        match_mode="has_none",
    ) == [south]


def test_build_grazing_chart_panels_builds_metric_split_payload():
    north = _paddock("north", "North")
    payload = {"paddocks": {"north": {"metrics": {"current_lsu": [1, 2]}}}}

    assert build_grazing_chart_panels(
        paddocks=[north],
        analytics_payload=payload,
        selected_metrics=["current_lsu"],
        split_mode="metric",
    ) == [
        {
            "id": "metric-current_lsu",
            "title": "Current LSU",
            "metric": "current_lsu",
            "y_axis_label": "LSU",
            "datasets": [{"key": "paddock:north", "label": "North", "values": [1, 2]}],
        }
    ]


def test_flatten_grazing_periods_returns_initial_period():
    north = _paddock("north", "North")
    period = {"start": "2026-01-01", "end": "2026-01-02"}
    payload = {"paddocks": {"north": {"periods": [period]}}}

    periods, initial = flatten_grazing_periods([north], payload)

    assert periods == [{"paddock_id": "north", "paddock_name": "North", "period": period}]
    assert initial == periods[0]
