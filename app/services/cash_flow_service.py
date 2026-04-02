from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import CashTransaction, CashTransactionLine

ZERO_DECIMAL = Decimal("0.00")
MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

STATEMENT_DEFINITIONS = {
    "overall": {
        "label": "Overall",
        "title": "Overall Business Cash Flow",
        "species_scope": None,
        "rows": (
            {"kind": "section", "label": "Livestock"},
            {"kind": "rollup", "label": "Sheep Income", "species_scope": "Sheep", "direction": "inflow"},
            {"kind": "rollup", "label": "Sheep Costs", "species_scope": "Sheep", "direction": "outflow"},
            {"kind": "rollup", "label": "Cattle Income", "species_scope": "Cattle", "direction": "inflow"},
            {"kind": "rollup", "label": "Cattle Costs", "species_scope": "Cattle", "direction": "outflow"},
            {"kind": "rollup", "label": "Goat Income", "species_scope": "Goat", "direction": "inflow"},
            {"kind": "rollup", "label": "Goat Costs", "species_scope": "Goat", "direction": "outflow"},
            {"kind": "section", "label": "Overheads"},
            {"kind": "category", "label": "Labour", "category_code": "labour", "species_scope": None},
            {"kind": "category", "label": "Diesel & Fuel", "category_code": "diesel_fuel", "species_scope": None},
            {
                "kind": "category",
                "label": "Repairs & Maintenance",
                "category_code": "repairs_maintenance",
                "species_scope": None,
            },
            {
                "kind": "category",
                "label": "Insurance & Admin",
                "category_code": "insurance_admin",
                "species_scope": None,
            },
            {"kind": "section", "label": "Capital"},
            {
                "kind": "category",
                "label": "Loan Repayments",
                "category_code": "loan_repayments",
                "species_scope": None,
            },
            {
                "kind": "category",
                "label": "Infrastructure (Fencing/Water)",
                "category_code": "infrastructure_fencing_water",
                "species_scope": None,
            },
            {
                "kind": "category",
                "label": "Machinery Purchases",
                "category_code": "machinery_purchases",
                "species_scope": None,
            },
            {"kind": "summary", "label": "Total Inflows", "direction": "inflow"},
            {"kind": "summary", "label": "Total Outflows", "direction": "outflow"},
            {"kind": "net", "label": "Net Cash Flow"},
        ),
    },
    "sheep": {
        "label": "Sheep",
        "title": "Sheep Cash Flow",
        "species_scope": "Sheep",
        "rows": (
            {"kind": "section", "label": "Costs"},
            {"kind": "category", "label": "Feed Cost", "category_code": "feed_cost", "species_scope": "Sheep"},
            {
                "kind": "category",
                "label": "Health Costs",
                "category_code": "health_costs",
                "species_scope": "Sheep",
            },
            {"kind": "category", "label": "Purchases", "category_code": "purchases", "species_scope": "Sheep"},
            {
                "kind": "category",
                "label": "Shearing Costs",
                "category_code": "shearing_costs",
                "species_scope": "Sheep",
            },
            {"kind": "category", "label": "Other Costs", "category_code": "other_costs", "species_scope": "Sheep"},
            {"kind": "section", "label": "Income"},
            {"kind": "category", "label": "Lamb Sales", "category_code": "lamb_sales", "species_scope": "Sheep"},
            {"kind": "category", "label": "Ewe Sales", "category_code": "ewe_sales", "species_scope": "Sheep"},
            {"kind": "category", "label": "Wool Income", "category_code": "wool_income", "species_scope": "Sheep"},
            {"kind": "category", "label": "Other Income", "category_code": "other_income", "species_scope": "Sheep"},
            {"kind": "summary", "label": "Total Inflows", "direction": "inflow"},
            {"kind": "summary", "label": "Total Outflows", "direction": "outflow"},
            {"kind": "net", "label": "Net Cash Flow"},
        ),
    },
    "cattle": {
        "label": "Cattle",
        "title": "Cattle Cash Flow",
        "species_scope": "Cattle",
        "rows": (
            {"kind": "section", "label": "Costs"},
            {"kind": "category", "label": "Feed Costs", "category_code": "feed_costs", "species_scope": "Cattle"},
            {
                "kind": "category",
                "label": "Health Costs",
                "category_code": "health_costs",
                "species_scope": "Cattle",
            },
            {"kind": "category", "label": "Purchases", "category_code": "purchases", "species_scope": "Cattle"},
            {
                "kind": "category",
                "label": "Bull Purchases",
                "category_code": "bull_purchases",
                "species_scope": "Cattle",
            },
            {"kind": "category", "label": "Other Costs", "category_code": "other_costs", "species_scope": "Cattle"},
            {"kind": "section", "label": "Income"},
            {"kind": "category", "label": "Calf Sales", "category_code": "calf_sales", "species_scope": "Cattle"},
            {"kind": "category", "label": "Cow Sales", "category_code": "cow_sales", "species_scope": "Cattle"},
            {
                "kind": "category",
                "label": "Bull/Oxen Sales",
                "category_code": "bull_oxen_sales",
                "species_scope": "Cattle",
            },
            {
                "kind": "category",
                "label": "Heifer Sales",
                "category_code": "heifer_sales",
                "species_scope": "Cattle",
            },
            {"kind": "category", "label": "Other Income", "category_code": "other_income", "species_scope": "Cattle"},
            {"kind": "summary", "label": "Total Inflows", "direction": "inflow"},
            {"kind": "summary", "label": "Total Outflows", "direction": "outflow"},
            {"kind": "net", "label": "Net Cash Flow"},
        ),
    },
    "goat": {
        "label": "Goats",
        "title": "Goat Cash Flow",
        "species_scope": "Goat",
        "rows": (
            {"kind": "section", "label": "Costs"},
            {"kind": "category", "label": "Feed Costs", "category_code": "feed_costs", "species_scope": "Goat"},
            {
                "kind": "category",
                "label": "Health Costs",
                "category_code": "health_costs",
                "species_scope": "Goat",
            },
            {"kind": "category", "label": "Purchases", "category_code": "purchases", "species_scope": "Goat"},
            {
                "kind": "category",
                "label": "Shearing Costs",
                "category_code": "shearing_costs",
                "species_scope": "Goat",
            },
            {"kind": "category", "label": "Other Costs", "category_code": "other_costs", "species_scope": "Goat"},
            {"kind": "section", "label": "Income"},
            {"kind": "category", "label": "Kid Sales", "category_code": "kid_sales", "species_scope": "Goat"},
            {"kind": "category", "label": "Doe Sales", "category_code": "doe_sales", "species_scope": "Goat"},
            {"kind": "category", "label": "Fiber Income", "category_code": "fiber_income", "species_scope": "Goat"},
            {"kind": "category", "label": "Other Income", "category_code": "other_income", "species_scope": "Goat"},
            {"kind": "summary", "label": "Total Inflows", "direction": "inflow"},
            {"kind": "summary", "label": "Total Outflows", "direction": "outflow"},
            {"kind": "net", "label": "Net Cash Flow"},
        ),
    },
}


class CashFlowService:
    @staticmethod
    def normalize_tab(value: str | None) -> str:
        tab = (value or "overall").strip().lower()
        if tab not in STATEMENT_DEFINITIONS:
            return "overall"
        return tab

    @staticmethod
    def _empty_month_totals() -> dict[int, Decimal]:
        return {month: ZERO_DECIMAL for month in range(1, 13)}

    @classmethod
    def _year_rows(cls, year: int) -> list[tuple[CashTransactionLine, date]]:
        start_date = date(year, 1, 1)
        end_date = date(year + 1, 1, 1)
        return (
            db.session.query(CashTransactionLine, CashTransaction.transaction_date)
            .join(CashTransaction, CashTransactionLine.transaction_id == CashTransaction.id)
            .filter(CashTransaction.transaction_date >= start_date)
            .filter(CashTransaction.transaction_date < end_date)
            .all()
        )

    @classmethod
    def _aggregate_year(cls, year: int) -> dict:
        category_month_totals = defaultdict(cls._empty_month_totals)
        species_direction_month_totals = defaultdict(cls._empty_month_totals)
        overall_direction_month_totals = defaultdict(cls._empty_month_totals)

        for line, transaction_date in cls._year_rows(year):
            month = transaction_date.month
            amount = Decimal(str(line.amount or 0))
            category_month_totals[(line.species_scope, line.category_code)][month] += amount
            overall_direction_month_totals[line.direction][month] += amount
            if line.species_scope:
                species_direction_month_totals[(line.species_scope, line.direction)][month] += amount

        return {
            "category_month_totals": category_month_totals,
            "species_direction_month_totals": species_direction_month_totals,
            "overall_direction_month_totals": overall_direction_month_totals,
        }

    @staticmethod
    def _months_to_list(month_totals: dict[int, Decimal]) -> list[Decimal]:
        return [month_totals.get(month, ZERO_DECIMAL) for month in range(1, 13)]

    @staticmethod
    def _annual_total(values: list[Decimal]) -> Decimal:
        return sum(values, ZERO_DECIMAL)

    @classmethod
    def _summary_months(cls, tab: str, direction: str, aggregates: dict) -> list[Decimal]:
        if tab == "overall":
            return cls._months_to_list(aggregates["overall_direction_month_totals"].get(direction, {}))

        species_scope = STATEMENT_DEFINITIONS[tab]["species_scope"]
        return cls._months_to_list(
            aggregates["species_direction_month_totals"].get((species_scope, direction), {})
        )

    @classmethod
    def build_statement(cls, tab: str, year: int, *, aggregates: dict | None = None) -> dict:
        normalized_tab = cls.normalize_tab(tab)
        definition = STATEMENT_DEFINITIONS[normalized_tab]
        aggregates = aggregates or cls._aggregate_year(year)

        inflow_values = cls._summary_months(normalized_tab, "inflow", aggregates)
        outflow_values = cls._summary_months(normalized_tab, "outflow", aggregates)
        net_values = [inflow - outflow for inflow, outflow in zip(inflow_values, outflow_values)]

        rows = []
        for row_definition in definition["rows"]:
            kind = row_definition["kind"]
            if kind == "section":
                rows.append(
                    {
                        "kind": "section",
                        "label": row_definition["label"],
                        "values": [None for _ in range(12)],
                        "annual": None,
                    }
                )
                continue

            if kind == "category":
                values = cls._months_to_list(
                    aggregates["category_month_totals"].get(
                        (row_definition["species_scope"], row_definition["category_code"]),
                        {},
                    )
                )
            elif kind == "rollup":
                values = cls._months_to_list(
                    aggregates["species_direction_month_totals"].get(
                        (row_definition["species_scope"], row_definition["direction"]),
                        {},
                    )
                )
            elif kind == "summary":
                values = inflow_values if row_definition["direction"] == "inflow" else outflow_values
            else:
                values = net_values

            rows.append(
                {
                    "kind": "net" if kind == "net" else ("summary" if kind == "summary" else "line"),
                    "label": row_definition["label"],
                    "values": values,
                    "annual": cls._annual_total(values),
                }
            )

        return {
            "tab": normalized_tab,
            "label": definition["label"],
            "title": definition["title"],
            "rows": rows,
            "summary": {
                "inflows": cls._annual_total(inflow_values),
                "outflows": cls._annual_total(outflow_values),
                "net": cls._annual_total(net_values),
            },
        }

    @classmethod
    def build_dashboard(cls, year: int) -> dict:
        aggregates = cls._aggregate_year(year)
        statements = {
            tab: cls.build_statement(tab, year, aggregates=aggregates) for tab in STATEMENT_DEFINITIONS
        }
        return {
            "months": [{"index": index + 1, "label": label} for index, label in enumerate(MONTH_LABELS)],
            "tab_options": [
                {"value": tab, "label": definition["label"]}
                for tab, definition in STATEMENT_DEFINITIONS.items()
            ],
            "statements": statements,
        }
