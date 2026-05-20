from app.modules.grazing.forms import (
    normalize_grazing_metric_selection,
    normalize_grazing_point_filter_match_mode,
    normalize_grazing_point_filter_metric,
    normalize_grazing_point_filter_operator,
    normalize_grazing_split_mode,
    parse_query_date,
)


def test_normalize_grazing_metric_selection_filters_duplicates_and_invalid_values():
    assert normalize_grazing_metric_selection(["current_lsu", "bad", "LSU_PER_HA", "current_lsu"]) == [
        "current_lsu",
        "lsu_per_ha",
    ]


def test_normalize_grazing_metric_selection_defaults_to_current_lsu():
    assert normalize_grazing_metric_selection(["bad"]) == ["current_lsu"]


def test_normalize_grazing_filter_controls_default_safely():
    assert normalize_grazing_split_mode("bad") == "metric"
    assert normalize_grazing_point_filter_metric("bad") == "current_lsu"
    assert normalize_grazing_point_filter_operator("bad") == "gt"
    assert normalize_grazing_point_filter_match_mode("bad") == "has_any"


def test_parse_query_date_accepts_blank_and_iso_date():
    assert parse_query_date(None) is None
    assert parse_query_date("2026-02-01").isoformat() == "2026-02-01"
