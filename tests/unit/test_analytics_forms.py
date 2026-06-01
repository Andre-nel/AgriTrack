from app.modules.analytics.forms import (
    journal_return_query_args,
    normalize_analytics_group_by,
    normalize_lsu_paddock_tracking_metric,
    normalize_lsu_paddock_tracking_plot_mode,
    normalize_journal_tags,
    parse_query_date,
)


def test_normalize_analytics_group_by_filters_duplicates_and_invalid_values():
    assert normalize_analytics_group_by(["Breed", "invalid", "breed", "farm"]) == [
        "breed",
        "farm",
    ]


def test_normalize_analytics_group_by_defaults_to_species():
    assert normalize_analytics_group_by(["", "invalid"]) == ["species"]


def test_normalize_lsu_paddock_tracking_controls_default_safely():
    assert normalize_lsu_paddock_tracking_metric("CURRENT_LSU") == "current_lsu"
    assert normalize_lsu_paddock_tracking_metric("head_count") == "head_count"
    assert normalize_lsu_paddock_tracking_metric("bad") == "lsu_per_ha"
    assert normalize_lsu_paddock_tracking_plot_mode("paddock") == "paddock"
    assert normalize_lsu_paddock_tracking_plot_mode("bad") == "overlay"


def test_normalize_journal_tags_deduplicates_and_normalizes():
    assert normalize_journal_tags([" Stock_Event ", "stock event", "", " Farm North "]) == [
        "stock event",
        "farm north",
    ]


def test_parse_query_date_accepts_blank_and_iso_date():
    assert parse_query_date("") is None
    assert parse_query_date("2026-01-31").isoformat() == "2026-01-31"


def test_journal_return_query_args_maps_non_empty_return_fields():
    assert journal_return_query_args(
        {
            "return_farm_id": "farm-1",
            "return_tag": " stock ",
            "return_start_date": "",
            "return_end_date": "2026-01-31",
        }
    ) == {"farm_id": "farm-1", "tag": "stock", "end_date": "2026-01-31"}
