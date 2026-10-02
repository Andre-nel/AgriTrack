import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, Mob, StockLedgerEntry
from app.models.stock_ledger import StockEventType


@pytest.fixture
def history_data(app):
    with app.app_context():
        first_farm = Farm(name="History North", timezone="SAST")
        second_farm = Farm(name="History South", timezone="SAST")
        db.session.add_all([first_farm, second_farm])
        db.session.flush()
        mob = Mob(farm_id=first_farm.id, name="History Mob", status="active")
        other_mob = Mob(farm_id=first_farm.id, name="Other History Mob", status="active")
        second_farm_mob = Mob(farm_id=second_farm.id, name="South History Mob", status="active")
        goat = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Dohne", sex="ram", age_class="young")
        db.session.add_all([mob, other_mob, second_farm_mob, goat, sheep])
        db.session.commit()
        return {
            "farm_id": str(first_farm.id),
            "second_farm_id": str(second_farm.id),
            "mob_id": str(mob.id),
            "other_mob_id": str(other_mob.id),
            "second_farm_mob_id": str(second_farm_mob.id),
            "goat_id": str(goat.id),
            "sheep_id": str(sheep.id),
        }


def _entry(
    data,
    event_time,
    event_type,
    quantity,
    note=None,
    *,
    mob_key="mob_id",
    group_key="goat_id",
    farm_key="farm_id",
):
    entry = StockLedgerEntry(
        farm_id=data[farm_key],
        mob_id=data[mob_key],
        animal_group_type_id=data[group_key],
        event_time=datetime.fromisoformat(event_time).replace(tzinfo=timezone.utc),
        event_type=event_type,
        quantity=quantity,
        note=note,
    )
    db.session.add(entry)
    return entry


def _history_url(data):
    return f"/mobs/{data['mob_id']}/stock-history"


def _chart(response):
    assert response.status_code == 200
    match = re.search(
        r'<script\b[^>]*\bid="stockTrackingChartData"[^>]*>(.*?)</script>',
        response.get_data(as_text=True),
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def _event_points(chart):
    return [
        point
        for dataset in chart["datasets"]
        for point in dataset["points"]
        if point["kind"] == "event"
    ]


def _events(chart):
    return [event for point in _event_points(chart) for event in point["events"]]


def test_mob_detail_links_to_stock_history_and_history_links_back(client, history_data):
    mob_id = history_data["mob_id"]
    detail = client.get(f"/mobs/{mob_id}")
    assert detail.status_code == 200
    assert f'href="/mobs/{mob_id}/stock-history"' in detail.get_data(as_text=True)

    history = client.get(_history_url(history_data))
    assert history.status_code == 200
    body = history.get_data(as_text=True)
    assert f'href="/mobs/{mob_id}"' in body
    assert "History Mob" in body
    assert 'name="farm_id"' not in body
    assert re.search(r'name="group_by"[^>]*value="farm"', body) is None
    assert "no stock" in body.lower()


def test_mob_history_uses_opening_balance_and_excludes_other_mobs(client, app, history_data):
    with app.app_context():
        _entry(history_data, "2025-12-31T08:00:00", StockEventType.purchase, 100, "Opening purchase")
        _entry(history_data, "2026-01-01T09:15:00", StockEventType.birth, 10, "Ten kids born")
        _entry(history_data, "2026-01-02T16:45:00", StockEventType.missing, 3, "Three unaccounted for")
        _entry(
            history_data,
            "2026-01-01T09:15:00",
            StockEventType.purchase,
            900,
            "Another mob's stock",
            mob_key="other_mob_id",
        )
        db.session.commit()

    chart = _chart(
        client.get(
            _history_url(history_data),
            query_string={"start_date": "2026-01-01", "end_date": "2026-01-03"},
        )
    )
    assert chart["time_zone"] == "UTC"
    assert [dataset["label"] for dataset in chart["datasets"]] == ["Goat"]
    points = chart["datasets"][0]["points"]
    assert points[0]["kind"] == "opening"
    assert points[0]["y"] == 100
    assert points[0]["events"] == []
    assert points[-1]["y"] == 107
    assert [
        (point["before"], point["delta"], point["y"]) for point in _event_points(chart)
    ] == [(100, 10, 110), (110, -3, 107)]
    assert {event["note"] for event in _events(chart)} == {"Ten kids born", "Three unaccounted for"}
    assert {event["mob_id"] for event in _events(chart)} == {history_data["mob_id"]}


def test_mob_history_loads_more_than_the_detail_ledger_limit(client, app, history_data):
    with app.app_context():
        _entry(history_data, "2025-12-01T08:00:00", StockEventType.purchase, 5, "Before range")
        for index in range(105):
            when = datetime(2026, 1, 1, 8) + timedelta(minutes=index)
            _entry(history_data, when.isoformat(), StockEventType.birth, 1, f"Birth {index + 1}")
        db.session.commit()

    chart = _chart(
        client.get(
            _history_url(history_data),
            query_string={"start_date": "2026-01-01", "end_date": "2026-01-01"},
        )
    )
    assert len(_events(chart)) == 105
    assert chart["datasets"][0]["points"][0]["y"] == 5
    assert chart["datasets"][0]["points"][-1]["y"] == 110


def test_mob_history_follows_stock_across_farms_and_ignores_farm_query(client, app, history_data):
    with app.app_context():
        mob = db.session.get(Mob, history_data["mob_id"])
        mob.farm_id = history_data["second_farm_id"]
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 20, "Bought on North")
        _entry(
            history_data,
            "2026-01-02T08:00:00",
            StockEventType.sale,
            2,
            "Sold on South",
            farm_key="second_farm_id",
        )
        _entry(
            history_data,
            "2026-01-02T08:00:00",
            StockEventType.purchase,
            50,
            "Other South mob",
            mob_key="second_farm_mob_id",
            farm_key="second_farm_id",
        )
        db.session.commit()

    chart = _chart(
        client.get(
            _history_url(history_data),
            query_string=[
                ("end_date", "2026-01-03"),
                ("farm_id", history_data["second_farm_id"]),
                ("group_by", "farm"),
                ("group_by", "species"),
            ],
        )
    )
    assert [dataset["label"] for dataset in chart["datasets"]] == ["Goat"]
    assert chart["datasets"][0]["points"][-1]["y"] == 18
    assert {(event["farm_name"], event["note"]) for event in _events(chart)} == {
        ("History North", "Bought on North"),
        ("History South", "Sold on South"),
    }


@pytest.mark.parametrize(
    "filter_name,filter_value",
    [("species", "Goat"), ("breed", "Angora"), ("sex", "ewe"), ("age_class", "adult")],
)
def test_mob_history_taxonomy_filters_limit_counts_and_notes(
    client, app, history_data, filter_name, filter_value
):
    with app.app_context():
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 12, "Goat purchase")
        _entry(
            history_data,
            "2026-01-01T09:00:00",
            StockEventType.purchase,
            40,
            "Sheep purchase",
            group_key="sheep_id",
        )
        db.session.commit()

    chart = _chart(
        client.get(
            _history_url(history_data),
            query_string={"end_date": "2026-01-02", filter_name: filter_value},
        )
    )
    assert len(chart["datasets"]) == 1
    assert chart["datasets"][0]["points"][-1]["y"] == 12
    assert [event["note"] for event in _events(chart)] == ["Goat purchase"]


@pytest.mark.parametrize("page", ["mob", "analytics"])
def test_explicitly_clearing_grouping_combines_species_into_total(client, app, history_data, page):
    with app.app_context():
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 12, "Goat purchase")
        _entry(
            history_data,
            "2026-01-01T08:00:00",
            StockEventType.purchase,
            8,
            "Sheep purchase",
            group_key="sheep_id",
        )
        db.session.commit()

    path = _history_url(history_data) if page == "mob" else "/analytics/stock-tracking"
    chart = _chart(
        client.get(path, query_string={"end_date": "2026-01-02", "group_by_applied": "1"})
    )
    assert len(chart["datasets"]) == 1
    assert chart["datasets"][0]["points"][-1]["y"] == 20
    assert len(_event_points(chart)) == 1
    assert {event["note"] for event in _events(chart)} == {"Goat purchase", "Sheep purchase"}


def test_analytics_farm_filters_limit_notes_and_allow_multiple_farms(client, app, history_data):
    with app.app_context():
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 12, "North notes")
        _entry(
            history_data,
            "2026-01-01T09:00:00",
            StockEventType.purchase,
            8,
            "South notes",
            mob_key="second_farm_mob_id",
            farm_key="second_farm_id",
        )
        db.session.commit()

    query = [("end_date", "2026-01-02"), ("farm_id", history_data["farm_id"])]
    selected = _chart(client.get("/analytics/stock-tracking", query_string=query))
    assert [event["note"] for event in _events(selected)] == ["North notes"]
    query.append(("farm_id", history_data["second_farm_id"]))
    combined = _chart(client.get("/analytics/stock-tracking", query_string=query))
    assert {event["note"] for event in _events(combined)} == {"North notes", "South notes"}
    assert combined["datasets"][0]["points"][-1]["y"] == 20


@pytest.mark.parametrize("page", ["mob", "analytics"])
def test_history_embeds_original_multiline_notes_without_executable_html(
    client, app, history_data, page
):
    note = (
        '</script><script>alert("stock-note")</script>\nCount checked: <b>two</b> & one.\n'
        + "Long recorded note. " * 150
    )
    with app.app_context():
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 12, note)
        _entry(history_data, "2026-01-01T09:00:00", StockEventType.sale, 1)
        db.session.commit()

    path = _history_url(history_data) if page == "mob" else "/analytics/stock-tracking"
    response = client.get(path, query_string={"end_date": "2026-01-02"})
    chart = _chart(response)
    assert _events(chart)[0]["note"] == note
    assert len(_events(chart)) == 2
    assert '<script>alert("stock-note")</script>' not in response.get_data(as_text=True)


@pytest.mark.parametrize("page", ["mob", "analytics"])
def test_simultaneous_changes_are_atomic_and_net_zero_missing_event_survives(
    client, app, history_data, page
):
    with app.app_context():
        _entry(history_data, "2025-12-31T08:00:00", StockEventType.purchase, 10)
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 6, "Purchase at count")
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.sale, 1, "Sale at count")
        _entry(history_data, "2026-01-01T10:00:00", StockEventType.missing, 2, "Two weg")
        _entry(history_data, "2026-01-01T10:00:00", StockEventType.adjustment_in, 2, "Two corrected")
        db.session.commit()

    path = _history_url(history_data) if page == "mob" else "/analytics/stock-tracking"
    chart = _chart(
        client.get(path, query_string={"start_date": "2026-01-01", "end_date": "2026-01-01"})
    )
    points = _event_points(chart)
    assert len(points) == 2
    assert [(point["before"], point["delta"], point["y"]) for point in points] == [
        (10, 5, 15),
        (15, 0, 15),
    ]
    assert all(len(point["events"]) == 2 for point in points)
    assert points[1]["highlight_missing"] is True
    assert {event["note"] for event in points[1]["events"]} == {"Two weg", "Two corrected"}


def test_history_date_bounds_include_end_day_and_default_to_first_entry(client, app, history_data):
    with app.app_context():
        _entry(history_data, "2025-12-31T23:59:00", StockEventType.purchase, 10, "Earliest history")
        _entry(history_data, "2026-01-03T23:59:59", StockEventType.sale, 2, "End of selected day")
        _entry(history_data, "2026-01-04T00:00:00", StockEventType.sale, 1, "Outside selected day")
        db.session.commit()

    chart = _chart(client.get(_history_url(history_data), query_string={"end_date": "2026-01-03"}))
    assert chart["min_x"] == int(datetime(2025, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)
    assert chart["datasets"][0]["points"][-1]["y"] == 8
    assert {event["note"] for event in _events(chart)} == {"Earliest history", "End of selected day"}


@pytest.mark.parametrize("head_count", [0, 7])
def test_mob_current_balance_reconciliation_is_scoped_and_includes_zero(
    client, app, history_data, head_count
):
    with app.app_context():
        _entry(history_data, "2026-01-01T08:00:00", StockEventType.purchase, 10, "Recorded history")
        db.session.add_all(
            [
                AnimalGroupBalance(
                    mob_id=history_data["mob_id"],
                    animal_group_type_id=history_data["goat_id"],
                    head_count=head_count,
                ),
                AnimalGroupBalance(
                    mob_id=history_data["other_mob_id"],
                    animal_group_type_id=history_data["goat_id"],
                    head_count=90,
                ),
            ]
        )
        db.session.commit()

    chart = _chart(client.get(_history_url(history_data)))
    assert len(chart["datasets"]) == 1
    assert chart["datasets"][0]["points"][-1]["y"] == head_count
    assert chart["datasets"][0]["opening_adjustment"] == head_count - 10
    assert [event["note"] for event in _events(chart)] == ["Recorded history"]


@pytest.mark.parametrize("page", ["mob", "analytics"])
@pytest.mark.parametrize(
    "query",
    [
        {"start_date": "2026-02-30"},
        {"start_date": "2026-02-02", "end_date": "2026-02-01"},
    ],
)
def test_invalid_history_dates_return_to_same_page_with_error(client, history_data, page, query):
    path = _history_url(history_data) if page == "mob" else "/analytics/stock-tracking"
    response = client.get(path, query_string=query)
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == path
    with client.session_transaction() as session:
        assert any(category == "error" for category, _message in session["_flashes"])


def test_mob_history_rejects_unknown_and_archived_mobs(client, app, history_data):
    assert client.get("/mobs/unknown-mob/stock-history").status_code == 404
    with app.app_context():
        db.session.get(Mob, history_data["mob_id"]).status = "archived"
        db.session.commit()
    assert client.get(_history_url(history_data)).status_code == 404


def test_mob_history_requires_login_when_web_auth_is_enabled(client, app, history_data):
    app.config["WEB_AUTH_REQUIRED"] = True
    path = _history_url(history_data)
    response = client.get(path)
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == [path]
