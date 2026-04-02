from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import CashTransaction, Farm
from app.services.cash_flow_service import CashFlowService
from app.services.finance_service import FinanceService


def _create_farm(name: str = "Finance Farm") -> Farm:
    farm = Farm(name=name, timezone="UTC")
    db.session.add(farm)
    db.session.flush()
    return farm


def _transaction_form(*, farm_id: str = "", transaction_date: str = "2026-01-15", description: str = "Cash entry"):
    values = MultiDict(
        [
            ("transaction_date", transaction_date),
            ("farm_id", farm_id),
            ("reference", "INV-100"),
            ("counterparty", "Counterparty"),
            ("description", description),
        ]
    )
    return values


def test_build_transaction_payload_requires_species_for_species_category(app):
    with app.app_context():
        form = _transaction_form()
        form.add("line_species_scope", "")
        form.add("line_category_code", "lamb_sales")
        form.add("line_amount", "1200")

        with pytest.raises(ValueError, match="species is required"):
            FinanceService.build_transaction_payload(form)


def test_build_transaction_payload_rejects_business_category_with_species(app):
    with app.app_context():
        form = _transaction_form()
        form.add("line_species_scope", "Sheep")
        form.add("line_category_code", "labour")
        form.add("line_amount", "950")

        with pytest.raises(ValueError, match="leave species blank"):
            FinanceService.build_transaction_payload(form)


def test_create_transaction_from_form_persists_lines_and_derives_directions(app):
    with app.app_context():
        farm = _create_farm()
        form = _transaction_form(farm_id=str(farm.id))
        form.add("line_species_scope", "Sheep")
        form.add("line_category_code", "lamb_sales")
        form.add("line_amount", "12500,50")
        form.add("line_species_scope", "")
        form.add("line_category_code", "labour")
        form.add("line_amount", "2500")

        FinanceService.create_transaction_from_form(form)
        db.session.commit()

        transaction = CashTransaction.query.first()
        assert transaction is not None
        assert transaction.farm_id == str(farm.id)
        assert transaction.reference == "INV-100"
        assert len(transaction.lines) == 2
        sorted_lines = sorted(transaction.lines, key=FinanceService.transaction_line_sort_key)
        assert sorted_lines[0].direction == "inflow"
        assert sorted_lines[0].species_scope == "Sheep"
        assert Decimal(str(sorted_lines[0].amount)) == Decimal("12500.50")
        assert sorted_lines[1].direction == "outflow"
        assert sorted_lines[1].species_scope is None


def test_cash_flow_service_aggregates_monthly_totals_and_excludes_overheads_from_species(app):
    with app.app_context():
        farm = _create_farm()

        january_form = _transaction_form(
            farm_id=str(farm.id),
            transaction_date="2026-01-05",
            description="January livestock sale",
        )
        january_form.add("line_species_scope", "Sheep")
        january_form.add("line_category_code", "lamb_sales")
        january_form.add("line_amount", "10000")
        january_form.add("line_species_scope", "Sheep")
        january_form.add("line_category_code", "feed_cost")
        january_form.add("line_amount", "3000")
        january_form.add("line_species_scope", "")
        january_form.add("line_category_code", "labour")
        january_form.add("line_amount", "700")
        FinanceService.create_transaction_from_form(january_form)

        february_form = _transaction_form(
            farm_id=str(farm.id),
            transaction_date="2026-02-10",
            description="Goat sale",
        )
        february_form.add("line_species_scope", "Goat")
        february_form.add("line_category_code", "kid_sales")
        february_form.add("line_amount", "2400")
        FinanceService.create_transaction_from_form(february_form)
        db.session.commit()

        overall = CashFlowService.build_statement("overall", 2026)
        sheep = CashFlowService.build_statement("sheep", 2026)

        overall_rows = {row["label"]: row for row in overall["rows"] if row["kind"] != "section"}
        sheep_rows = {row["label"]: row for row in sheep["rows"] if row["kind"] != "section"}

        assert overall_rows["Sheep Income"]["values"][0] == Decimal("10000.00")
        assert overall_rows["Labour"]["values"][0] == Decimal("700.00")
        assert overall_rows["Total Outflows"]["annual"] == Decimal("3700.00")
        assert overall_rows["Net Cash Flow"]["annual"] == Decimal("8700.00")

        assert sheep_rows["Feed Cost"]["values"][0] == Decimal("3000.00")
        assert sheep_rows["Total Outflows"]["annual"] == Decimal("3000.00")
        assert sheep_rows["Net Cash Flow"]["annual"] == Decimal("7000.00")
