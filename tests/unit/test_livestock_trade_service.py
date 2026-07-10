from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import CashTransaction, Farm
from app.services.livestock_trade_service import LivestockTradeService


def _farm(name: str) -> Farm:
    farm = Farm(name=name, timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    return farm


def _weighted_form(*, farm_ids: list[str], trade_type: str = "sale", trade_date: str = "2026-01-10"):
    values = MultiDict(
        [
            ("trade_type", trade_type),
            ("trade_animal_mode", "lamb"),
            ("trade_date", trade_date),
            ("counterparty", "Weighted Buyer"),
            ("reference", "LAMBS-1"),
            ("vat_rate", "15"),
            ("breed", "Dorper"),
            ("sex", "mixed"),
            ("age_class", "lamb"),
            ("price_per_kg", "40"),
            ("weight_kg", "30"),
            ("weight_kg", "35"),
            ("weights_csv", "40, 45"),
            ("notes", "Weighted trade"),
        ]
    )
    for farm_id in farm_ids:
        values.add("farm_ids", farm_id)
    return values


def _calf_form(*, farm_ids: list[str], trade_date: str = "2026-01-11"):
    values = _weighted_form(farm_ids=farm_ids, trade_date=trade_date)
    values["trade_animal_mode"] = "calf"
    values["counterparty"] = "Calf Buyer"
    values["reference"] = "CALVES-1"
    values["breed"] = "Bonsmara"
    values["sex"] = "mixed"
    values["age_class"] = "calf"
    values["price_per_kg"] = "50"
    values.setlist("weight_kg", ["100"])
    values["weights_csv"] = ""
    return values


def _goat_form(*, farm_ids: list[str], trade_type: str = "purchase", trade_date: str = "2026-01-12"):
    values = MultiDict(
        [
            ("trade_type", trade_type),
            ("trade_animal_mode", "goat"),
            ("trade_date", trade_date),
            ("counterparty", "Goat Seller"),
            ("reference", "GOATS-1"),
            ("vat_rate", "0.15"),
            ("breed", "Boer"),
            ("sex", "ewe"),
            ("age_class", "adult"),
            ("price_per_head", "1000"),
            ("goat_count", "3"),
            ("hair_lengths", "short, long"),
        ]
    )
    for farm_id in farm_ids:
        values.add("farm_ids", farm_id)
    return values


def test_weighted_lamb_trade_parses_weights_vat_and_syncs_cash_transaction(app):
    with app.app_context():
        first = _farm("Trade Farm A")
        second = _farm("Trade Farm B")

        trade = LivestockTradeService.create_trade_from_form(
            _weighted_form(farm_ids=[str(first.id), str(second.id)])
        )
        db.session.commit()

        stats = LivestockTradeService.trade_statistics(trade)
        assert stats["count"] == 4
        assert stats["total_weight"] == Decimal("150.000")
        assert stats["average_weight"] == Decimal("37.500")
        assert stats["base_total"] == Decimal("6000.00")
        assert stats["vat_amount"] == Decimal("900.00")
        assert stats["gross_total"] == Decimal("6900.00")
        assert stats["total_money"] == Decimal("6000.00")
        assert stats["average_price_per_animal"] == Decimal("1500.00")

        transaction = trade.cash_transaction
        assert transaction.farm_id is None
        assert transaction.counterparty == "Weighted Buyer"
        assert len(transaction.lines) == 1
        line = transaction.lines[0]
        assert line.category_code == "lamb_sales"
        assert line.direction == "inflow"
        assert line.species_scope == "Sheep"
        assert Decimal(str(line.amount)) == Decimal("6900.00")
        assert line.source_type == LivestockTradeService.SOURCE_TYPE
        assert line.source_id == str(trade.id)


def test_goat_purchase_uses_per_head_pricing_and_single_farm_cash_attribution(app):
    with app.app_context():
        farm = _farm("Single Goat Farm")

        trade = LivestockTradeService.create_trade_from_form(_goat_form(farm_ids=[str(farm.id)]))
        db.session.commit()

        stats = LivestockTradeService.trade_statistics(trade)
        assert stats["count"] == 3
        assert stats["total_weight"] is None
        assert stats["base_total"] == Decimal("3000.00")
        assert stats["vat_amount"] == Decimal("450.00")
        assert stats["gross_total"] == Decimal("3450.00")
        assert stats["total_money"] == Decimal("3000.00")
        assert stats["unit_price"] == Decimal("1000.00")
        assert stats["gross_unit_price"] == Decimal("1150.00")

        transaction = trade.cash_transaction
        assert transaction.farm_id == str(farm.id)
        assert transaction.lines[0].category_code == "purchases"
        assert transaction.lines[0].direction == "outflow"
        assert transaction.lines[0].species_scope == "Goat"


def test_livestock_trade_validation_rejects_missing_weights_and_hair_lengths(app):
    with app.app_context():
        farm = _farm("Validation Trade Farm")
        missing_weights = _weighted_form(farm_ids=[str(farm.id)])
        missing_weights.setlist("weight_kg", [""])
        missing_weights["weights_csv"] = ""

        with pytest.raises(ValueError, match="At least one animal weight"):
            LivestockTradeService.build_trade_payload(missing_weights)

        missing_hair = _goat_form(farm_ids=[str(farm.id)])
        missing_hair["hair_lengths"] = ""

        with pytest.raises(ValueError, match="Hair lengths is required"):
            LivestockTradeService.build_trade_payload(missing_hair)


def test_livestock_trade_period_report_summarizes_and_orders_chart_points(app):
    with app.app_context():
        farm = _farm("Report Trade Farm")
        LivestockTradeService.create_trade_from_form(
            _weighted_form(farm_ids=[str(farm.id)], trade_date="2026-01-10")
        )
        LivestockTradeService.create_trade_from_form(
            _calf_form(farm_ids=[str(farm.id)], trade_date="2026-01-11")
        )
        LivestockTradeService.create_trade_from_form(
            _goat_form(farm_ids=[str(farm.id)], trade_date="2026-01-12")
        )
        db.session.commit()

        report = LivestockTradeService.build_period_report(
            start_date=LivestockTradeService.parse_date("2026-01-01", "Start"),
            end_date=LivestockTradeService.parse_date("2026-01-31", "End"),
        )

        assert report["summary"]["trade_count"] == 3
        assert report["summary"]["animal_count"] == 8
        assert report["summary"]["total_weight"] == Decimal("250.000")
        assert report["summary"]["total_sales_income"] == Decimal("11000.00")
        assert report["summary"]["total_purchase_spend"] == Decimal("3000.00")
        assert report["chart_payload"]["labels"] == [
            "2026-01-10 Sale Lambs",
            "2026-01-11 Sale Calves",
            "2026-01-12 Purchase Goats",
        ]
        price_datasets = report["chart_payload"]["panels"][0]["datasets"]
        assert [dataset["label"] for dataset in price_datasets] == [
            "Sheep R/kg",
            "Cattle R/kg",
            "Goat R/head",
        ]
        assert price_datasets[0]["values"] == [40.0, None, None]
        assert price_datasets[1]["values"] == [None, 50.0, None]
        assert price_datasets[2]["values"] == [None, None, 1000.0]
        assert report["chart_payload"]["panels"][1]["datasets"][0]["values"] == [4.0, None, None]
        assert report["chart_payload"]["panels"][1]["datasets"][1]["values"] == [None, 1.0, None]
        assert report["chart_payload"]["panels"][1]["datasets"][2]["values"] == [None, None, 3.0]

        assert CashTransaction.query.count() == 3
