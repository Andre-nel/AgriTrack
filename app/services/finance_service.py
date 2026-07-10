from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func

from app.extensions import db
from app.models import CashTransaction, CashTransactionLine, Farm

ZERO_DECIMAL = Decimal("0.00")
MONEY_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class CashCategoryDefinition:
    code: str
    label: str
    direction: str
    allowed_species: tuple[str, ...] = ()
    group_label: str = "Species Categories"

    @property
    def overall_only(self) -> bool:
        return len(self.allowed_species) == 0


CATEGORY_DEFINITIONS = (
    CashCategoryDefinition("lamb_sales", "Lamb Sales", "inflow", ("Sheep",), "Species Inflows"),
    CashCategoryDefinition("ewe_sales", "Ewe Sales", "inflow", ("Sheep",), "Species Inflows"),
    CashCategoryDefinition("wool_income", "Wool Income", "inflow", ("Sheep",), "Species Inflows"),
    CashCategoryDefinition("calf_sales", "Calf Sales", "inflow", ("Cattle",), "Species Inflows"),
    CashCategoryDefinition("cow_sales", "Cow Sales", "inflow", ("Cattle",), "Species Inflows"),
    CashCategoryDefinition(
        "bull_oxen_sales",
        "Bull/Oxen Sales",
        "inflow",
        ("Cattle",),
        "Species Inflows",
    ),
    CashCategoryDefinition("heifer_sales", "Heifer Sales", "inflow", ("Cattle",), "Species Inflows"),
    CashCategoryDefinition("kid_sales", "Kid Sales", "inflow", ("Goat",), "Species Inflows"),
    CashCategoryDefinition("goat_sales", "Goat Sales", "inflow", ("Goat",), "Species Inflows"),
    CashCategoryDefinition("doe_sales", "Doe Sales", "inflow", ("Goat",), "Species Inflows"),
    CashCategoryDefinition("fiber_income", "Fiber Income", "inflow", ("Goat",), "Species Inflows"),
    CashCategoryDefinition(
        "other_income",
        "Other Income",
        "inflow",
        ("Sheep", "Cattle", "Goat"),
        "Species Inflows",
    ),
    CashCategoryDefinition("feed_cost", "Feed Cost", "outflow", ("Sheep",), "Species Outflows"),
    CashCategoryDefinition(
        "feed_costs",
        "Feed Costs",
        "outflow",
        ("Cattle", "Goat"),
        "Species Outflows",
    ),
    CashCategoryDefinition(
        "health_costs",
        "Health Costs",
        "outflow",
        ("Sheep", "Cattle", "Goat"),
        "Species Outflows",
    ),
    CashCategoryDefinition(
        "purchases",
        "Purchases",
        "outflow",
        ("Sheep", "Cattle", "Goat"),
        "Species Outflows",
    ),
    CashCategoryDefinition(
        "shearing_costs",
        "Shearing Costs",
        "outflow",
        ("Sheep", "Goat"),
        "Species Outflows",
    ),
    CashCategoryDefinition(
        "other_costs",
        "Other Costs",
        "outflow",
        ("Sheep", "Cattle", "Goat"),
        "Species Outflows",
    ),
    CashCategoryDefinition(
        "bull_purchases",
        "Bull Purchases",
        "outflow",
        ("Cattle",),
        "Species Outflows",
    ),
    CashCategoryDefinition("labour", "Labour", "outflow", (), "Business Overheads"),
    CashCategoryDefinition("diesel_fuel", "Diesel & Fuel", "outflow", (), "Business Overheads"),
    CashCategoryDefinition(
        "repairs_maintenance",
        "Repairs & Maintenance",
        "outflow",
        (),
        "Business Overheads",
    ),
    CashCategoryDefinition(
        "insurance_admin",
        "Insurance & Admin",
        "outflow",
        (),
        "Business Overheads",
    ),
    CashCategoryDefinition(
        "loan_repayments",
        "Loan Repayments",
        "outflow",
        (),
        "Business Overheads",
    ),
    CashCategoryDefinition(
        "infrastructure_fencing_water",
        "Infrastructure (Fencing/Water)",
        "outflow",
        (),
        "Business Overheads",
    ),
    CashCategoryDefinition(
        "machinery_purchases",
        "Machinery Purchases",
        "outflow",
        (),
        "Business Overheads",
    ),
)

CATEGORY_DEFINITIONS_BY_CODE = {definition.code: definition for definition in CATEGORY_DEFINITIONS}


class FinanceService:
    MAX_REFERENCE_LENGTH = 120
    MAX_COUNTERPARTY_LENGTH = 120
    MAX_DESCRIPTION_LENGTH = 500

    @staticmethod
    def optional_text(value: str | None, field_name: str, max_length: int) -> str | None:
        normalized = " ".join((value or "").strip().split())
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def required_text(value: str | None, field_name: str, max_length: int) -> str:
        normalized = " ".join((value or "").strip().split())
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def parse_transaction_date(value: str | None) -> date:
        text = (value or "").strip()
        if not text:
            raise ValueError("Transaction date is required")
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("Transaction date must be valid (YYYY-MM-DD)") from exc

    @staticmethod
    def parse_year(value: str | None, *, default: int | None = None) -> int:
        text = (value or "").strip()
        if not text:
            return default if default is not None else date.today().year
        try:
            year = int(text)
        except ValueError as exc:
            raise ValueError("Year must be valid") from exc
        if year < 2000 or year > 2100:
            raise ValueError("Year must be between 2000 and 2100")
        return year

    @staticmethod
    def parse_money(value: str | None, *, field_name: str = "Amount") -> Decimal:
        normalized = (value or "").strip().replace("R", "").replace(" ", "")
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if "," in normalized and "." not in normalized:
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
        try:
            amount = Decimal(normalized)
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} must be a valid amount") from exc
        if amount <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    @staticmethod
    def normalize_species_scope(value: str | None) -> str | None:
        normalized = " ".join((value or "").strip().lower().split())
        if not normalized:
            return None
        mapping = {
            "sheep": "Sheep",
            "cattle": "Cattle",
            "goat": "Goat",
            "goats": "Goat",
        }
        species_scope = mapping.get(normalized)
        if not species_scope:
            raise ValueError("Species scope must be Sheep, Cattle, Goat, or blank")
        return species_scope

    @staticmethod
    def normalize_category_code(value: str | None) -> str:
        normalized = "_".join((value or "").strip().lower().split())
        if not normalized:
            raise ValueError("Category is required")
        if normalized not in CATEGORY_DEFINITIONS_BY_CODE:
            raise ValueError("Category is invalid")
        return normalized

    @classmethod
    def category_definition(cls, category_code: str) -> CashCategoryDefinition:
        return CATEGORY_DEFINITIONS_BY_CODE[category_code]

    @classmethod
    def category_label(cls, category_code: str) -> str:
        definition = CATEGORY_DEFINITIONS_BY_CODE.get(category_code)
        return definition.label if definition else category_code.replace("_", " ").title()

    @staticmethod
    def direction_label(direction: str) -> str:
        return "Inflow" if direction == "inflow" else "Outflow"

    @staticmethod
    def species_label(species_scope: str | None) -> str:
        return species_scope or "Business-wide"

    @staticmethod
    def format_currency(value) -> str:
        amount = Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        sign = "-" if amount < 0 else ""
        text = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",")
        return f"{sign}R{text}"

    @classmethod
    def category_select_groups(cls) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for definition in CATEGORY_DEFINITIONS:
            groups.setdefault(definition.group_label, []).append(
                {
                    "value": definition.code,
                    "label": definition.label,
                    "direction": definition.direction,
                }
            )
        return [{"label": label, "options": options} for label, options in groups.items()]

    @staticmethod
    def species_scope_options() -> list[dict]:
        return [
            {"value": "", "label": "Business-wide / Unallocated"},
            {"value": "Sheep", "label": "Sheep"},
            {"value": "Cattle", "label": "Cattle"},
            {"value": "Goat", "label": "Goats"},
        ]

    @classmethod
    def available_years(cls, selected_year: int | None = None) -> list[int]:
        values = (
            db.session.query(func.strftime("%Y", CashTransaction.transaction_date))
            .filter(CashTransaction.transaction_date.isnot(None))
            .distinct()
            .all()
        )
        years = {int(value[0]) for value in values if value[0]}
        years.add(selected_year or date.today().year)
        years.add(date.today().year)
        return sorted(years, reverse=True)

    @classmethod
    def transaction_line_sort_key(cls, line: CashTransactionLine) -> tuple[int, str]:
        order_lookup = {definition.code: index for index, definition in enumerate(CATEGORY_DEFINITIONS)}
        return (order_lookup.get(line.category_code, 999), str(line.id))

    @staticmethod
    def _optional_farm_id(value: str | None) -> str | None:
        farm_id = (value or "").strip()
        if not farm_id:
            return None
        farm = Farm.query.filter_by(id=farm_id).first()
        if not farm:
            raise ValueError("Selected farm is invalid")
        return str(farm.id)

    @staticmethod
    def _value_at(values: list[str], index: int) -> str:
        return values[index] if index < len(values) else ""

    @classmethod
    def _build_line_payloads(cls, source) -> list[dict]:
        species_values = source.getlist("line_species_scope")
        category_values = source.getlist("line_category_code")
        amount_values = source.getlist("line_amount")
        max_length = max(len(species_values), len(category_values), len(amount_values))

        lines = []
        for index in range(max_length):
            species_raw = cls._value_at(species_values, index)
            category_raw = cls._value_at(category_values, index)
            amount_raw = cls._value_at(amount_values, index)
            if not any(value.strip() for value in (species_raw, category_raw, amount_raw)):
                continue

            line_number = index + 1
            category_code = cls.normalize_category_code(category_raw)
            species_scope = cls.normalize_species_scope(species_raw)
            definition = cls.category_definition(category_code)

            if definition.overall_only:
                if species_scope is not None:
                    raise ValueError(f"Line {line_number} must leave species blank for business-wide categories")
            else:
                if species_scope is None:
                    raise ValueError(f"Line {line_number} species is required for the selected category")
                if species_scope not in definition.allowed_species:
                    raise ValueError(
                        f"Line {line_number} category {definition.label} is not valid for {species_scope}"
                    )

            amount = cls.parse_money(amount_raw, field_name=f"Line {line_number} amount")
            lines.append(
                {
                    "category_code": category_code,
                    "direction": definition.direction,
                    "species_scope": species_scope,
                    "amount": amount,
                    "source_type": None,
                    "source_id": None,
                }
            )

        if not lines:
            raise ValueError("At least one transaction line is required")
        return lines

    @classmethod
    def build_transaction_payload(cls, source) -> dict:
        return {
            "transaction_date": cls.parse_transaction_date(source.get("transaction_date")),
            "farm_id": cls._optional_farm_id(source.get("farm_id")),
            "reference": cls.optional_text(
                source.get("reference"),
                "Reference",
                cls.MAX_REFERENCE_LENGTH,
            ),
            "counterparty": cls.optional_text(
                source.get("counterparty"),
                "Counterparty",
                cls.MAX_COUNTERPARTY_LENGTH,
            ),
            "description": cls.required_text(
                source.get("description"),
                "Description",
                cls.MAX_DESCRIPTION_LENGTH,
            ),
            "lines": cls._build_line_payloads(source),
        }

    @classmethod
    def apply_transaction_payload(cls, transaction: CashTransaction, payload: dict) -> CashTransaction:
        transaction.transaction_date = payload["transaction_date"]
        transaction.farm_id = payload["farm_id"]
        transaction.reference = payload["reference"]
        transaction.counterparty = payload["counterparty"]
        transaction.description = payload["description"]
        transaction.lines = [
            CashTransactionLine(
                category_code=line["category_code"],
                direction=line["direction"],
                species_scope=line["species_scope"],
                amount=line["amount"],
                source_type=line["source_type"],
                source_id=line["source_id"],
            )
            for line in payload["lines"]
        ]
        return transaction

    @classmethod
    def create_transaction_from_form(cls, source) -> CashTransaction:
        transaction = CashTransaction()
        cls.apply_transaction_payload(transaction, cls.build_transaction_payload(source))
        db.session.add(transaction)
        return transaction

    @classmethod
    def update_transaction_from_form(cls, transaction: CashTransaction, source) -> CashTransaction:
        cls.apply_transaction_payload(transaction, cls.build_transaction_payload(source))
        return transaction

    @staticmethod
    def delete_transaction(transaction: CashTransaction) -> None:
        db.session.delete(transaction)
