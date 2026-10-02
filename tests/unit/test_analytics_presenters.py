from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, Mob
from app.models.stock_ledger import StockEventType
from app.modules.analytics.presenters import (
    build_stock_tracking_chart_data,
    current_stock_totals_by_group,
)


def _ledger_entry(
    event_date: str, event_type: StockEventType, quantity: int, *,
    note: str | None = None, entry_id: str | None = None,
    mob_id: str = "north-mob", mob_name: str = "North Mob",
):
    return SimpleNamespace(
        id=entry_id or f"{event_date}-{event_type.value}",
        event_time=datetime.fromisoformat(event_date),
        event_type=event_type,
        quantity=quantity,
        note=note,
        mob_id=mob_id,
        mob=SimpleNamespace(name=mob_name),
    )


def _group_type(species: str = "Cattle"):
    return SimpleNamespace(
        species=species,
        breed="Angus",
        sex="Cow",
        age_class="Adult",
    )


def _chart(rows, **kwargs):
    options = {
        "group_by_fields": ["species"],
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 3),
        **kwargs,
    }
    return build_stock_tracking_chart_data(ledger_rows=rows, **options)


def _event_points(chart, dataset_index=0):
    return [point for point in chart["datasets"][dataset_index]["points"] if point["kind"] == "event"]


def _epoch_ms(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000


def test_stock_tracking_keeps_separate_intraday_changes_and_sparse_boundaries():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10), "North", _group_type()),
        (_ledger_entry("2026-01-01T14:00:00", StockEventType.sale, 3), "North", _group_type()),
    ]

    chart, latest_totals = _chart(rows)

    assert chart["min_x"] == _epoch_ms("2026-01-01T00:00:00")
    assert chart["max_x"] == _epoch_ms("2026-01-04T00:00:00")
    assert chart["time_zone"] == "UTC"
    dataset = chart["datasets"][0]
    assert dataset["label"] == "Cattle"
    assert dataset["opening_adjustment"] == 0
    assert [point["kind"] for point in dataset["points"]] == ["opening", "event", "event", "closing"]
    assert [point["y"] for point in dataset["points"]] == [0, 10, 7, 7]
    assert [(point["before"], point["delta"]) for point in _event_points(chart)] == [(0, 10), (10, -3)]
    assert _event_points(chart)[0]["x"] != _event_points(chart)[1]["x"]
    assert latest_totals == [{"label": "Cattle", "head_count": 7}]


def test_stock_tracking_reconciles_latest_using_opening_adjustment_without_fake_events():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10), "North", _group_type()),
    ]

    chart, latest_totals = _chart(rows, reconcile_to_current_totals={("Cattle",): 12})

    dataset = chart["datasets"][0]
    assert dataset["opening_adjustment"] == 2
    assert [point["y"] for point in dataset["points"]] == [2, 12, 12]
    assert len(_event_points(chart)) == 1
    assert (_event_points(chart)[0]["before"], _event_points(chart)[0]["delta"]) == (2, 10)
    assert latest_totals == [{"label": "Cattle", "head_count": 12}]


def test_stock_tracking_preserves_note_and_event_context():
    note = "  Count verified\n<script>alert('shortage')</script> & stock present.  "
    entry = _ledger_entry(
        "2026-01-01T08:00:00.123456", StockEventType.purchase, 10,
        note=note, entry_id="purchase-1",
    )

    chart, _ = _chart([(entry, "North Farm", _group_type())])

    assert _event_points(chart)[0]["events"] == [{
        "id": "purchase-1",
        "event_time": "2026-01-01T08:00:00.123456Z",
        "event_type": "purchase",
        "event_label": "Purchase",
        "delta": 10,
        "mob_id": "north-mob",
        "mob_name": "North Mob",
        "farm_name": "North Farm",
        "animal_type": "Cattle | Angus | Cow | Adult",
        "note": note,
        "highlight_missing": False,
        "highlight_reasons": [],
    }]


def test_stock_tracking_normalizes_timezones_and_groups_same_instant_atomically():
    rows = [
        (_ledger_entry("2026-01-01T10:00:00+02:00", StockEventType.transfer_in, 7, entry_id="b"), "North", _group_type()),
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.transfer_out, 7, entry_id="a"), "North", _group_type()),
    ]

    chart, totals = _chart(rows)

    points = _event_points(chart)
    assert len(points) == 1
    assert points[0]["x"] == _epoch_ms("2026-01-01T08:00:00")
    assert (points[0]["before"], points[0]["y"], points[0]["delta"]) == (0, 0, 0)
    assert [event["id"] for event in points[0]["events"]] == ["a", "b"]
    assert {event["event_time"] for event in points[0]["events"]} == {"2026-01-01T08:00:00Z"}
    assert totals == [{"label": "Cattle", "head_count": 0}]


def test_stock_tracking_keeps_microsecond_changes_distinct_and_orders_them():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00.123457", StockEventType.sale, 1), "North", _group_type()),
        (_ledger_entry("2026-01-01T08:00:00.123456", StockEventType.purchase, 2), "North", _group_type()),
    ]

    chart, _ = _chart(rows)

    points = _event_points(chart)
    assert len(points) == 2
    assert points[0]["x"] < points[1]["x"]
    assert points[0]["x"] % 1 != 0
    assert [point["y"] for point in points] == [2, 1]
    assert [point["events"][0]["event_time"] for point in points] == [
        "2026-01-01T08:00:00.123456Z", "2026-01-01T08:00:00.123457Z",
    ]


def test_stock_tracking_applies_utc_date_window_and_carries_baseline_without_notes():
    rows = [
        (_ledger_entry("2026-01-01T01:00:00+02:00", StockEventType.purchase, 10, note="Short opening"), "North", _group_type()),
        (_ledger_entry("2026-01-01T00:00:00Z", StockEventType.sale, 1, note="At start"), "North", _group_type()),
        (_ledger_entry("2026-01-03T23:59:59.999999Z", StockEventType.sale, 2, note="Last instant"), "North", _group_type()),
        (_ledger_entry("2026-01-04T00:00:00Z", StockEventType.purchase, 100, note="Excluded"), "North", _group_type()),
    ]

    chart, totals = _chart(rows)

    points = chart["datasets"][0]["points"]
    assert [point["y"] for point in points] == [10, 9, 7, 7]
    assert points[0]["x"] == points[1]["x"] == chart["min_x"]
    assert points[-2]["x"] < points[-1]["x"] == chart["max_x"]
    for boundary in (points[0], points[-1]):
        assert boundary["events"] == []
        assert boundary["highlight_missing"] is False
        assert boundary["delta"] == 0
    assert [point["events"][0]["note"] for point in _event_points(chart)] == ["At start", "Last instant"]
    assert totals == [{"label": "Cattle", "head_count": 7}]


def test_stock_tracking_explicit_empty_current_totals_reconcile_to_zero():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.sale, 5), "North", _group_type()),
    ]

    chart, totals = _chart(rows, reconcile_to_current_totals={})

    dataset = chart["datasets"][0]
    assert dataset["opening_adjustment"] == 5
    assert [point["y"] for point in dataset["points"]] == [5, 0, 0]
    assert totals == [{"label": "Cattle", "head_count": 0}]


def test_stock_tracking_current_stock_without_ledger_has_only_boundaries():
    chart, totals = _chart([], reconcile_to_current_totals={("Cattle",): 12})

    dataset = chart["datasets"][0]
    assert dataset["opening_adjustment"] == 12
    assert [point["kind"] for point in dataset["points"]] == ["opening", "closing"]
    assert [point["y"] for point in dataset["points"]] == [12, 12]
    assert all(point["events"] == [] and not point["highlight_missing"] for point in dataset["points"])
    assert totals == [{"label": "Cattle", "head_count": 12}]


def test_stock_tracking_empty_history_returns_valid_range_without_series():
    chart, totals = _chart([], reconcile_to_current_totals={})

    assert chart["datasets"] == []
    assert chart["min_x"] < chart["max_x"]
    assert totals == []


def test_stock_tracking_no_group_fields_combines_all_animal_types_as_total():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10), "North", _group_type()),
        (_ledger_entry("2026-01-01T09:00:00", StockEventType.purchase, 5), "South", _group_type("Sheep")),
    ]

    chart, totals = _chart(rows, group_by_fields=[])

    assert [dataset["label"] for dataset in chart["datasets"]] == ["Total"]
    assert [point["y"] for point in _event_points(chart)] == [10, 15]
    assert totals == [{"label": "Total", "head_count": 15}]


def test_stock_tracking_keeps_series_notes_and_highlights_separate_at_same_time():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10, note="SHORT at yard"), "North", _group_type()),
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 7, note="Other farm note", mob_id="south-mob", mob_name="South Mob"), "South", _group_type()),
    ]

    chart, _ = _chart(rows, group_by_fields=["species", "farm"])

    assert [dataset["label"] for dataset in chart["datasets"]] == ["Species: Cattle | Farm: North", "Species: Cattle | Farm: South"]
    north, south = _event_points(chart, 0)[0], _event_points(chart, 1)[0]
    assert north["highlight_missing"] is True
    assert south["highlight_missing"] is False
    assert [event["note"] for event in north["events"]] == ["SHORT at yard"]
    assert [event["mob_name"] for event in south["events"]] == ["South Mob"]


@pytest.mark.parametrize("keyword", ["missing", "kort", "short", "mis", "weg"])
def test_stock_tracking_highlights_each_case_insensitive_whole_word(keyword):
    entry = _ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 3, note=f"Checked: {keyword.upper()}!")

    chart, _ = _chart([(entry, "North", _group_type())])

    point = _event_points(chart)[0]
    assert point["highlight_missing"] is True
    assert point["events"][0]["highlight_reasons"] == [f"Note keyword: {keyword}"]


@pytest.mark.parametrize("note", [None, "", "shortage", "mistake", "missingness", "verkort", "weggaan", "éshorté"])
def test_stock_tracking_does_not_highlight_absent_or_partial_keywords(note):
    entry = _ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 3, note=note)

    chart, _ = _chart([(entry, "North", _group_type())])

    point = _event_points(chart)[0]
    assert point["highlight_missing"] is False
    assert point["events"][0]["highlight_reasons"] == []


def test_stock_tracking_missing_event_is_flagged_without_note():
    entry = _ledger_entry("2026-01-01T08:00:00", StockEventType.missing, 3)

    chart, _ = _chart([(entry, "North", _group_type())])

    point = _event_points(chart)[0]
    assert point["highlight_missing"] is True
    assert point["events"][0]["highlight_reasons"] == ["Missing stock recorded"]
    assert point["events"][0]["note"] == ""


def test_stock_tracking_preserves_distinct_match_reasons_per_event():
    entry = _ledger_entry(
        "2026-01-01T08:00:00", StockEventType.missing, 3,
        note="not missing; SHORT-stock, short stock; KORT, mis, weg.",
    )

    chart, _ = _chart([(entry, "North", _group_type())])

    assert _event_points(chart)[0]["events"][0]["highlight_reasons"] == [
        "Missing stock recorded", "Note keyword: missing", "Note keyword: short",
        "Note keyword: kort", "Note keyword: mis", "Note keyword: weg",
    ]


def test_stock_tracking_highlights_atomic_net_zero_group_and_keeps_zero_series():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.missing, 3, entry_id="a"), "North", _group_type()),
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.adjustment_in, 3, entry_id="b", note="Recount"), "North", _group_type()),
    ]

    chart, totals = _chart(rows)

    assert [point["y"] for point in chart["datasets"][0]["points"]] == [0, 0, 0]
    point = _event_points(chart)[0]
    assert point["delta"] == 0
    assert point["highlight_missing"] is True
    assert [event["highlight_missing"] for event in point["events"]] == [True, False]
    assert point["events"][1]["highlight_reasons"] == []
    assert totals == [{"label": "Cattle", "head_count": 0}]


def test_current_stock_totals_can_be_locked_to_mob(app):
    farm = Farm(name="Stock history farm", timezone="SAST")
    group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
    db.session.add_all([farm, group])
    db.session.flush()
    first = Mob(farm_id=farm.id, name="First", status="active")
    second = Mob(farm_id=farm.id, name="Second", status="active")
    db.session.add_all([first, second])
    db.session.flush()
    db.session.add_all([
        AnimalGroupBalance(mob_id=first.id, animal_group_type_id=group.id, head_count=8),
        AnimalGroupBalance(mob_id=second.id, animal_group_type_id=group.id, head_count=13),
    ])
    db.session.flush()
    filters = {"species": "", "breed": "", "sex": "", "age_class": "", "farm_ids": []}

    assert current_stock_totals_by_group(filters, ["species"]) == {("Goat",): 21}
    assert current_stock_totals_by_group(filters, ["species"], mob_id=first.id) == {("Goat",): 8}
    assert current_stock_totals_by_group(filters, [], mob_id=first.id) == {(): 8}
    filters["species"] = "Cattle"
    assert current_stock_totals_by_group(filters, [], mob_id=first.id) == {}
