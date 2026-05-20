from datetime import date, datetime
from types import SimpleNamespace

from app.models.stock_ledger import StockEventType
from app.modules.analytics.presenters import build_stock_tracking_chart_data


def _ledger_entry(event_date: str, event_type: StockEventType, quantity: int):
    return SimpleNamespace(
        event_time=datetime.fromisoformat(event_date),
        event_type=event_type,
        quantity=quantity,
    )


def _group_type(species: str = "Cattle"):
    return SimpleNamespace(
        species=species,
        breed="Angus",
        sex="Cow",
        age_class="Adult",
    )


def test_build_stock_tracking_chart_data_accumulates_daily_deltas():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10), "North", _group_type()),
        (_ledger_entry("2026-01-03T08:00:00", StockEventType.sale, 3), "North", _group_type()),
    ]

    chart, latest_totals = build_stock_tracking_chart_data(
        ledger_rows=rows,
        group_by_fields=["species"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
    )

    assert chart == {
        "labels": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "datasets": [{"label": "Cattle", "values": [10, 10, 7]}],
    }
    assert latest_totals == [{"label": "Cattle", "head_count": 7}]


def test_build_stock_tracking_chart_data_reconciles_latest_total():
    rows = [
        (_ledger_entry("2026-01-01T08:00:00", StockEventType.purchase, 10), "North", _group_type()),
    ]

    chart, latest_totals = build_stock_tracking_chart_data(
        ledger_rows=rows,
        group_by_fields=["species"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        reconcile_to_current_totals={("Cattle",): 12},
    )

    assert chart["datasets"] == [{"label": "Cattle", "values": [12, 12]}]
    assert latest_totals == [{"label": "Cattle", "head_count": 12}]
