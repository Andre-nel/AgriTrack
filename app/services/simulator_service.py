from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    Mob,
    SimulatorExpense,
    SimulatorFarm,
    SimulatorRevenueAssumption,
    SimulatorScenario,
    SimulatorStockDetail,
)
from app.services.finance_service import FinanceService
from app.services.stock_service import StockService

ZERO_DECIMAL = Decimal("0.00")
MONEY_QUANT = Decimal("0.01")
MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ADULT_FEMALE_SEX_BY_SPECIES = {
    "Cattle": "cow",
    "Sheep": "ewe",
    "Goat": "ewe",
}


@dataclass(frozen=True)
class SimulatorExpenseCategory:
    code: str
    label: str


EXPENSE_CATEGORIES = (
    SimulatorExpenseCategory("loan", "Loan"),
    SimulatorExpenseCategory("wages", "Wages"),
    SimulatorExpenseCategory("diesel_petrol", "Diesel, Petrol"),
    SimulatorExpenseCategory("gas", "Gas"),
    SimulatorExpenseCategory("vehicle_maintenance", "Vehicle Maintenance"),
    SimulatorExpenseCategory("mielies_feeds", "Mielies, Feeding Lick/Feeds"),
    SimulatorExpenseCategory("fencing", "Fencing"),
    SimulatorExpenseCategory("water", "Water"),
    SimulatorExpenseCategory("housing", "Housing"),
    SimulatorExpenseCategory("infrastructure", "Infrastructure"),
    SimulatorExpenseCategory("machinery_purchase", "Machinery Purchase"),
    SimulatorExpenseCategory("animal_medicine", "Animal Medicine"),
    SimulatorExpenseCategory("shearing_costs", "Shearing Costs"),
    SimulatorExpenseCategory("cement", "Cement"),
    SimulatorExpenseCategory("other", "Other"),
)
EXPENSE_CATEGORY_BY_CODE = {category.code: category for category in EXPENSE_CATEGORIES}
RECURRENCE_OPTIONS = (
    ("monthly", "Monthly"),
    ("yearly", "Yearly"),
    ("once_off", "Once-off"),
)
PAYMENT_INTERVAL_OPTIONS = (
    (1, "Monthly"),
    (3, "Quarterly"),
    (6, "Biannual"),
    (12, "Annual"),
)


class SimulatorService:
    MAX_NAME_LENGTH = 120
    MAX_NOTES_LENGTH = 1000
    MAX_BREED_LENGTH = 50
    MAX_LABEL_LENGTH = 120

    @staticmethod
    def format_currency(value) -> str:
        return FinanceService.format_currency(value)

    @staticmethod
    def month_options() -> list[dict]:
        return [{"value": index + 1, "label": label} for index, label in enumerate(MONTH_LABELS)]

    @staticmethod
    def expense_category_options() -> list[dict]:
        return [{"value": category.code, "label": category.label} for category in EXPENSE_CATEGORIES]

    @staticmethod
    def recurrence_options() -> list[dict]:
        return [{"value": value, "label": label} for value, label in RECURRENCE_OPTIONS]

    @staticmethod
    def payment_interval_options() -> list[dict]:
        return [{"value": value, "label": label} for value, label in PAYMENT_INTERVAL_OPTIONS]

    @staticmethod
    def species_options() -> list[str]:
        return ["Cattle", "Sheep", "Goat"]

    @staticmethod
    def _getlist(source, key: str) -> list[str]:
        getlist = getattr(source, "getlist", None)
        if callable(getlist):
            return [str(value).strip() for value in getlist(key) if str(value).strip()]
        value = source.get(key) if hasattr(source, "get") else None
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def active_farms() -> list[Farm]:
        return Farm.query.filter_by(active=True).order_by(Farm.name.asc()).all()

    @staticmethod
    def _farm_scope_was_submitted(source) -> bool:
        if not hasattr(source, "get"):
            return False
        if (source.get("farm_scope_submitted") or "").strip():
            return True
        if SimulatorService._getlist(source, "farm_ids"):
            return True
        return bool((source.get("new_farm_name") or "").strip())

    @classmethod
    def scenario_farm_options(cls, scenario: SimulatorScenario) -> list[dict]:
        targets = sorted(
            scenario.farm_targets,
            key=lambda target: (target.display_name.lower(), str(target.id)),
        )
        if targets:
            return [
                {
                    "value": str(target.id),
                    "label": target.display_name,
                    "is_future": target.farm_id is None,
                    "farm_id": str(target.farm_id) if target.farm_id else None,
                }
                for target in targets
            ]
        return [
            {
                "value": str(farm.id),
                "label": farm.name,
                "is_future": False,
                "farm_id": str(farm.id),
            }
            for farm in cls.active_farms()
        ]

    @staticmethod
    def _text(value: str | None, field_name: str, max_length: int, *, required: bool = True) -> str | None:
        normalized = " ".join((value or "").strip().split())
        if not normalized:
            if required:
                raise ValueError(f"{field_name} is required")
            return None
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def parse_year(value: str | None, *, default: int | None = None) -> int:
        return FinanceService.parse_year(value, default=default)

    @staticmethod
    def parse_money(value: str | None, *, field_name: str) -> Decimal:
        return FinanceService.parse_money(value, field_name=field_name)

    @staticmethod
    def parse_decimal(value: str | None, *, field_name: str, allow_zero: bool = False) -> Decimal:
        text = (value or "").strip().replace("%", "").replace(" ", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} must be valid") from exc
        if amount < 0 or (amount == 0 and not allow_zero):
            comparator = "0 or greater" if allow_zero else "greater than 0"
            raise ValueError(f"{field_name} must be {comparator}")
        return amount

    @staticmethod
    def parse_percent(value: str | None, *, field_name: str, default: Decimal = ZERO_DECIMAL) -> Decimal:
        text = (value or "").strip()
        if not text:
            return default
        amount = SimulatorService.parse_decimal(text, field_name=field_name, allow_zero=True)
        return amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    def parse_int(
        value: str | None,
        *,
        field_name: str,
        minimum: int | None = None,
        maximum: int | None = None,
        allow_zero: bool = True,
    ) -> int:
        text = (value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a whole number") from exc
        if not allow_zero and parsed == 0:
            raise ValueError(f"{field_name} cannot be zero")
        if minimum is not None and parsed < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}")
        if maximum is not None and parsed > maximum:
            raise ValueError(f"{field_name} must be at most {maximum}")
        return parsed

    @classmethod
    def normalize_species(cls, value: str | None) -> str:
        return StockService.normalize_species(value or "")

    @classmethod
    def normalize_breed(cls, value: str | None) -> str:
        return cls._text(value, "Breed", cls.MAX_BREED_LENGTH)

    @classmethod
    def normalize_category_code(cls, value: str | None) -> str:
        code = "_".join((value or "").strip().lower().split())
        if code not in EXPENSE_CATEGORY_BY_CODE:
            raise ValueError("Expense category is invalid")
        return code

    @staticmethod
    def normalize_recurrence(value: str | None) -> str:
        recurrence = (value or "").strip().lower()
        if recurrence == "annual":
            recurrence = "yearly"
        allowed = {option[0] for option in RECURRENCE_OPTIONS}
        if recurrence not in allowed:
            raise ValueError("Recurrence is invalid")
        return recurrence

    @classmethod
    def create_scenario(cls, source) -> SimulatorScenario:
        scenario = SimulatorScenario()
        cls.update_scenario(scenario, source)
        db.session.add(scenario)
        db.session.flush()
        targets = cls._create_scenario_farm_targets(scenario, source)
        db.session.flush()
        cls.seed_stock_details_from_live(scenario, farm_targets=targets)
        return scenario

    @classmethod
    def _create_scenario_farm_targets(cls, scenario: SimulatorScenario, source) -> list[SimulatorFarm]:
        if cls._farm_scope_was_submitted(source):
            farm_ids = cls._getlist(source, "farm_ids")
        else:
            farm_ids = [str(farm.id) for farm in cls.active_farms()]

        targets: list[SimulatorFarm] = []
        seen_farm_ids: set[str] = set()
        seen_names: set[str] = set()
        for farm_id in farm_ids:
            if farm_id in seen_farm_ids:
                continue
            farm = Farm.query.filter_by(id=farm_id, active=True).first()
            if farm is None:
                raise ValueError("Selected farm is invalid")
            normalized_name = farm.name.strip().lower()
            if normalized_name in seen_names:
                raise ValueError("Scenario farm names must be unique")
            target = SimulatorFarm(
                scenario_id=scenario.id,
                farm_id=str(farm.id),
                name=farm.name,
            )
            db.session.add(target)
            targets.append(target)
            seen_farm_ids.add(farm_id)
            seen_names.add(normalized_name)

        future_name = cls._text(
            source.get("new_farm_name"),
            "Future farm name",
            cls.MAX_NAME_LENGTH,
            required=False,
        )
        if future_name:
            normalized_name = future_name.lower()
            if normalized_name in seen_names:
                raise ValueError("Scenario farm names must be unique")
            target = SimulatorFarm(
                scenario_id=scenario.id,
                farm_id=None,
                name=future_name,
            )
            db.session.add(target)
            targets.append(target)

        return targets

    @classmethod
    def update_scenario(cls, scenario: SimulatorScenario, source) -> SimulatorScenario:
        scenario.name = cls._text(source.get("name"), "Scenario name", cls.MAX_NAME_LENGTH)
        scenario.projection_year = cls.parse_year(
            source.get("projection_year"),
            default=date.today().year,
        )
        scenario.notes = cls._text(
            source.get("notes"),
            "Notes",
            cls.MAX_NOTES_LENGTH,
            required=False,
        )
        scenario.income_inflation_rate = cls.parse_percent(
            source.get("income_inflation_rate"),
            field_name="Income inflation rate",
        )
        scenario.expense_inflation_rate = cls.parse_percent(
            source.get("expense_inflation_rate"),
            field_name="Expense inflation rate",
        )
        return scenario

    @classmethod
    def upsert_revenue_assumption(cls, scenario: SimulatorScenario, source) -> SimulatorRevenueAssumption:
        species = cls.normalize_species(source.get("species"))
        breed = cls.normalize_breed(source.get("breed"))
        amount = cls.parse_money(
            source.get("yearly_revenue_per_adult_female"),
            field_name="Yearly revenue per adult female",
        )
        assumption = SimulatorRevenueAssumption.query.filter_by(
            scenario_id=scenario.id,
            species=species,
            breed=breed,
        ).first()
        if assumption is None:
            assumption = SimulatorRevenueAssumption(
                scenario_id=scenario.id,
                species=species,
                breed=breed,
            )
            db.session.add(assumption)
        assumption.yearly_revenue_per_adult_female = amount
        return assumption

    @classmethod
    def seed_stock_details_from_live(
        cls,
        scenario: SimulatorScenario,
        *,
        farm_targets: list[SimulatorFarm] | None = None,
    ) -> None:
        cls.clear_stock_details(scenario)
        db.session.flush()
        targets = farm_targets if farm_targets is not None else list(scenario.farm_targets)
        farm_target_by_farm_id = {
            str(target.farm_id): str(target.id)
            for target in targets
            if target.farm_id
        }
        limit_to_targets = farm_targets is not None or bool(targets)
        for (farm_id, species, breed), head_count in sorted(cls.live_adult_female_stock().items()):
            if limit_to_targets and farm_id not in farm_target_by_farm_id:
                continue
            if head_count < 0:
                continue
            db.session.add(
                SimulatorStockDetail(
                    scenario_id=scenario.id,
                    simulator_farm_id=farm_target_by_farm_id.get(farm_id),
                    farm_id=farm_id,
                    species=species,
                    breed=breed,
                    adult_female_count=head_count,
                )
            )

    @staticmethod
    def clear_stock_details(scenario: SimulatorScenario) -> None:
        SimulatorStockDetail.query.filter_by(scenario_id=scenario.id).delete(
            synchronize_session=False,
        )
        db.session.expire(scenario, ["stock_details"])

    @classmethod
    def _ensure_scenario_farm_for_farm(cls, scenario: SimulatorScenario, farm: Farm) -> SimulatorFarm:
        target = SimulatorFarm.query.filter_by(
            scenario_id=scenario.id,
            farm_id=str(farm.id),
        ).first()
        if target is not None:
            return target
        target = SimulatorFarm(
            scenario_id=scenario.id,
            farm_id=str(farm.id),
            name=farm.name,
        )
        db.session.add(target)
        db.session.flush()
        return target

    @classmethod
    def _scenario_farm_from_value(
        cls,
        scenario: SimulatorScenario,
        value: str | None,
        *,
        required: bool,
    ) -> SimulatorFarm | None:
        target_id = (value or "").strip()
        if not target_id:
            if required:
                raise ValueError("Farm is required")
            return None

        target = SimulatorFarm.query.filter_by(
            id=target_id,
            scenario_id=scenario.id,
        ).first()
        if target is not None:
            return target

        farm = Farm.query.filter_by(id=target_id, active=True).first()
        if farm is not None:
            return cls._ensure_scenario_farm_for_farm(scenario, farm)

        message = "Farm is required" if required else "Selected farm is invalid"
        raise ValueError(message)

    @classmethod
    def _required_scenario_farm(cls, scenario: SimulatorScenario, source) -> SimulatorFarm:
        value = (source.get("simulator_farm_id") or source.get("farm_id") or "").strip()
        return cls._scenario_farm_from_value(scenario, value, required=True)

    @classmethod
    def _optional_scenario_farm(cls, scenario: SimulatorScenario, source) -> SimulatorFarm | None:
        value = (source.get("simulator_farm_id") or source.get("farm_id") or "").strip()
        return cls._scenario_farm_from_value(scenario, value, required=False)

    @classmethod
    def upsert_stock_detail(cls, scenario: SimulatorScenario, source) -> SimulatorStockDetail:
        scenario_farm = cls._required_scenario_farm(scenario, source)
        species = cls.normalize_species(source.get("species"))
        breed = cls.normalize_breed(source.get("breed"))
        adult_female_count = cls.parse_int(
            source.get("adult_female_count"),
            field_name="Adult female count",
            minimum=0,
        )

        stock_query = SimulatorStockDetail.query.filter_by(
            scenario_id=scenario.id,
            simulator_farm_id=scenario_farm.id,
            species=species,
            breed=breed,
        )
        if scenario_farm.farm_id:
            stock_query = stock_query.union(
                SimulatorStockDetail.query.filter_by(
                    scenario_id=scenario.id,
                    simulator_farm_id=None,
                    farm_id=str(scenario_farm.farm_id),
                    species=species,
                    breed=breed,
                )
            )
        stock_detail = stock_query.first()
        if stock_detail is None:
            stock_detail = SimulatorStockDetail(
                scenario_id=scenario.id,
                simulator_farm_id=scenario_farm.id,
                farm_id=str(scenario_farm.farm_id) if scenario_farm.farm_id else None,
                species=species,
                breed=breed,
            )
            db.session.add(stock_detail)
        else:
            stock_detail.simulator_farm_id = scenario_farm.id
            stock_detail.farm_id = str(scenario_farm.farm_id) if scenario_farm.farm_id else None
        stock_detail.adult_female_count = adult_female_count
        return stock_detail

    @classmethod
    def update_stock_detail(cls, stock_detail: SimulatorStockDetail, source) -> SimulatorStockDetail:
        scenario = db.session.get(SimulatorScenario, stock_detail.scenario_id)
        scenario_farm = cls._required_scenario_farm(scenario, source)
        species = cls.normalize_species(source.get("species"))
        breed = cls.normalize_breed(source.get("breed"))
        duplicate = SimulatorStockDetail.query.filter(
            SimulatorStockDetail.scenario_id == stock_detail.scenario_id,
            SimulatorStockDetail.simulator_farm_id == scenario_farm.id,
            SimulatorStockDetail.species == species,
            SimulatorStockDetail.breed == breed,
            SimulatorStockDetail.id != stock_detail.id,
        ).first()
        if duplicate is not None:
            raise ValueError("A stock row already exists for this farm, species, and breed")
        stock_detail.simulator_farm_id = scenario_farm.id
        stock_detail.farm_id = str(scenario_farm.farm_id) if scenario_farm.farm_id else None
        stock_detail.species = species
        stock_detail.breed = breed
        stock_detail.adult_female_count = cls.parse_int(
            source.get("adult_female_count"),
            field_name="Adult female count",
            minimum=0,
        )
        return stock_detail

    @classmethod
    def create_expense(cls, scenario: SimulatorScenario, source) -> SimulatorExpense:
        expense = SimulatorExpense(scenario_id=scenario.id)
        cls.update_expense(expense, scenario, source)
        db.session.add(expense)
        return expense

    @classmethod
    def update_expense(cls, expense: SimulatorExpense, scenario: SimulatorScenario, source) -> SimulatorExpense:
        expense_type = (source.get("expense_type") or "standard").strip().lower()
        if expense_type not in {"standard", "loan"}:
            raise ValueError("Expense type is invalid")
        scenario_farm = cls._optional_scenario_farm(scenario, source)
        category_code = cls.normalize_category_code(source.get("category_code"))
        if expense_type == "loan":
            category_code = "loan"
        label = cls._text(source.get("label"), "Expense label", cls.MAX_LABEL_LENGTH)

        expense.scenario_id = scenario.id
        expense.simulator_farm_id = scenario_farm.id if scenario_farm else None
        expense.farm_id = str(scenario_farm.farm_id) if scenario_farm and scenario_farm.farm_id else None
        expense.category_code = category_code
        expense.label = label
        expense.expense_type = expense_type
        if expense_type == "loan":
            expense.amount = None
            expense.recurrence = None
            expense.start_month = None
            expense.loan_principal = cls.parse_money(source.get("loan_principal"), field_name="Loan principal")
            expense.annual_interest_rate = cls.parse_decimal(
                source.get("annual_interest_rate"),
                field_name="Annual interest rate",
                allow_zero=True,
            )
            expense.remaining_term_years = cls.parse_decimal(
                source.get("remaining_term_years"),
                field_name="Remaining term years",
            )
            expense.payment_interval_months = cls.parse_payment_interval(source.get("payment_interval_months"))
            expense.first_payment_month = cls.parse_int(
                source.get("first_payment_month"),
                field_name="First payment month",
                minimum=1,
                maximum=12,
            )
        else:
            expense.amount = cls.parse_money(source.get("amount"), field_name="Expense amount")
            expense.recurrence = cls.normalize_recurrence(source.get("recurrence"))
            expense.start_month = None
            expense.loan_principal = None
            expense.annual_interest_rate = None
            expense.remaining_term_years = None
            expense.payment_interval_months = None
            expense.first_payment_month = None

        return expense

    @staticmethod
    def parse_payment_interval(value: str | None) -> int:
        try:
            interval = int((value or "").strip())
        except ValueError as exc:
            raise ValueError("Payment interval is invalid") from exc
        if interval not in {option[0] for option in PAYMENT_INTERVAL_OPTIONS}:
            raise ValueError("Payment interval is invalid")
        return interval

    @classmethod
    def live_adult_female_stock(cls) -> dict[tuple[str, str, str], int]:
        rows = (
            db.session.query(
                Mob.farm_id,
                AnimalGroupType.species,
                AnimalGroupType.breed,
                func.sum(AnimalGroupBalance.head_count),
            )
            .join(Mob, AnimalGroupBalance.mob_id == Mob.id)
            .join(AnimalGroupType, AnimalGroupBalance.animal_group_type_id == AnimalGroupType.id)
            .join(Farm, Mob.farm_id == Farm.id)
            .filter(Farm.active.is_(True))
            .filter(Mob.status == "active")
            .filter(AnimalGroupType.age_class == "adult")
            .filter(
                or_(
                    and_(AnimalGroupType.species == "Cattle", AnimalGroupType.sex == "cow"),
                    and_(AnimalGroupType.species == "Sheep", AnimalGroupType.sex == "ewe"),
                    and_(AnimalGroupType.species == "Goat", AnimalGroupType.sex == "ewe"),
                )
            )
            .group_by(Mob.farm_id, AnimalGroupType.species, AnimalGroupType.breed)
            .all()
        )
        return {
            (str(farm_id), species, breed): int(head_count or 0)
            for farm_id, species, breed, head_count in rows
        }

    @classmethod
    def _projection_farm_targets(cls, scenario: SimulatorScenario) -> list[dict]:
        targets = sorted(
            scenario.farm_targets,
            key=lambda target: (target.display_name.lower(), str(target.id)),
        )
        if targets:
            return [
                {
                    "id": str(target.id),
                    "farm_id": str(target.farm_id) if target.farm_id else None,
                    "name": target.display_name,
                    "is_future": target.farm_id is None,
                }
                for target in targets
            ]
        return [
            {
                "id": str(farm.id),
                "farm_id": str(farm.id),
                "name": farm.name,
                "is_future": False,
            }
            for farm in cls.active_farms()
        ]

    @staticmethod
    def _target_key_for_farm_link(
        *,
        simulator_farm_id,
        farm_id,
        target_id_by_farm_id: dict[str, str],
    ) -> str | None:
        if simulator_farm_id:
            return str(simulator_farm_id)
        if farm_id:
            farm_key = str(farm_id)
            return target_id_by_farm_id.get(farm_key, farm_key)
        return None

    @classmethod
    def _stock_details_by_target(
        cls,
        scenario: SimulatorScenario,
        target_id_by_farm_id: dict[str, str],
    ) -> dict[str, list[SimulatorStockDetail]]:
        rows: dict[str, list[SimulatorStockDetail]] = defaultdict(list)
        for stock_detail in scenario.stock_details:
            target_key = cls._target_key_for_farm_link(
                simulator_farm_id=stock_detail.simulator_farm_id,
                farm_id=stock_detail.farm_id,
                target_id_by_farm_id=target_id_by_farm_id,
            )
            if target_key:
                rows[target_key].append(stock_detail)
        for farm_rows in rows.values():
            farm_rows.sort(key=lambda item: (item.species, item.breed.lower()))
        return rows

    @staticmethod
    def _revenue_lookup(scenario: SimulatorScenario) -> dict[tuple[str, str], Decimal]:
        return {
            (assumption.species, assumption.breed): Decimal(str(assumption.yearly_revenue_per_adult_female))
            for assumption in scenario.revenue_assumptions
        }

    @classmethod
    def standard_expense_annual_amount(cls, expense: SimulatorExpense) -> Decimal:
        amount = Decimal(str(expense.amount or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        if expense.recurrence == "monthly":
            return (amount * Decimal("12")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        return amount

    @staticmethod
    def _inflation_multiplier(rate_percent, year_offset: int) -> Decimal:
        rate = Decimal(str(rate_percent or 0)) / Decimal("100")
        return (Decimal("1") + rate) ** year_offset

    @classmethod
    def inflated_amount(cls, amount, rate_percent, year_offset: int) -> Decimal:
        return (Decimal(str(amount or 0)) * cls._inflation_multiplier(rate_percent, year_offset)).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def _loan_term_months(expense: SimulatorExpense) -> int:
        years = Decimal(str(expense.remaining_term_years or 0))
        months = (years * Decimal("12")).to_integral_value(rounding=ROUND_CEILING)
        return max(1, int(months))

    @staticmethod
    def _period_count(term_months: int, interval_months: int) -> int:
        return max(1, (term_months + interval_months - 1) // interval_months)

    @classmethod
    def loan_payment_amount(cls, expense: SimulatorExpense) -> Decimal:
        principal = Decimal(str(expense.loan_principal or 0))
        interval_months = int(expense.payment_interval_months or 1)
        periods = cls._period_count(cls._loan_term_months(expense), interval_months)
        annual_rate = Decimal(str(expense.annual_interest_rate or 0)) / Decimal("100")
        period_rate = annual_rate * Decimal(interval_months) / Decimal("12")
        if period_rate == 0:
            payment = principal / Decimal(periods)
        else:
            payment = principal * period_rate / (Decimal("1") - (Decimal("1") + period_rate) ** (-periods))
        return payment.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def loan_expense_annual_amount(cls, expense: SimulatorExpense) -> Decimal:
        return cls.loan_expense_annual_amount_for_year(expense, 0)

    @classmethod
    def loan_expense_annual_amount_for_year(cls, expense: SimulatorExpense, year_offset: int) -> Decimal:
        payment = cls.loan_payment_amount(expense)
        interval_months = int(expense.payment_interval_months or 1)
        periods = cls._period_count(cls._loan_term_months(expense), interval_months)
        month = int(expense.first_payment_month or 1)
        scheduled = 0
        paid_in_year = 0
        while scheduled < periods:
            payment_year = (month - 1) // 12
            if payment_year == year_offset:
                paid_in_year += 1
            if payment_year > year_offset:
                break
            month += interval_months
            scheduled += 1
        return (payment * Decimal(paid_in_year)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def expense_annual_amount(cls, expense: SimulatorExpense) -> Decimal:
        if expense.expense_type == "loan":
            return cls.loan_expense_annual_amount(expense)
        return cls.standard_expense_annual_amount(expense)

    @classmethod
    def expense_annual_amount_for_year(
        cls,
        expense: SimulatorExpense,
        year_offset: int,
        *,
        expense_inflation_rate,
    ) -> Decimal:
        if expense.expense_type == "loan":
            return cls.loan_expense_annual_amount_for_year(expense, year_offset)
        if expense.recurrence == "once_off" and year_offset > 0:
            return ZERO_DECIMAL
        return cls.inflated_amount(
            cls.standard_expense_annual_amount(expense),
            expense_inflation_rate,
            year_offset,
        )

    @staticmethod
    def _health_summary(annual_net: Decimal) -> dict:
        if annual_net > 0:
            label = "Healthy"
            css_class = "ok"
        elif annual_net == 0:
            label = "Break-even"
            css_class = "warn"
        else:
            label = "Unhealthy"
            css_class = "danger"
        return {
            "label": label,
            "class": css_class,
        }

    @classmethod
    def build_projection(cls, scenario: SimulatorScenario) -> dict:
        farm_targets = cls._projection_farm_targets(scenario)
        target_id_by_farm_id = {
            target["farm_id"]: target["id"]
            for target in farm_targets
            if target["farm_id"]
        }
        stock_details_by_target = cls._stock_details_by_target(scenario, target_id_by_farm_id)
        revenue_rates = cls._revenue_lookup(scenario)

        farm_results = []
        overall_income = ZERO_DECIMAL
        overall_farm_expenses = ZERO_DECIMAL
        farm_expense_rows = defaultdict(list)
        business_expense_rows = []

        for expense in scenario.expenses:
            annual = cls.expense_annual_amount_for_year(
                expense,
                0,
                expense_inflation_rate=scenario.expense_inflation_rate,
            )
            row = {
                "id": str(expense.id),
                "label": expense.label,
                "category": cls.category_label(expense.category_code),
                "farm_name": (
                    expense.simulator_farm.display_name
                    if expense.simulator_farm
                    else expense.farm.name
                    if expense.farm
                    else "Business-wide"
                ),
                "annual": annual,
                "payment_amount": cls.loan_payment_amount(expense) if expense.expense_type == "loan" else None,
            }
            target_key = cls._target_key_for_farm_link(
                simulator_farm_id=expense.simulator_farm_id,
                farm_id=expense.farm_id,
                target_id_by_farm_id=target_id_by_farm_id,
            )
            if target_key:
                farm_expense_rows[target_key].append(row)
            else:
                business_expense_rows.append(row)

        for target in farm_targets:
            target_id = target["id"]
            stock_rows = []
            annual_income = ZERO_DECIMAL
            for stock_detail in stock_details_by_target.get(target_id, []):
                revenue_per_head = revenue_rates.get(
                    (stock_detail.species, stock_detail.breed),
                    ZERO_DECIMAL,
                )
                adult_female_count = int(stock_detail.adult_female_count or 0)
                annual_income = (
                    annual_income + Decimal(adult_female_count) * revenue_per_head
                ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
                stock_rows.append(
                    {
                        "id": str(stock_detail.id),
                        "simulator_farm_id": target_id,
                        "farm_id": target["farm_id"],
                        "species": stock_detail.species,
                        "breed": stock_detail.breed,
                        "adult_female_count": adult_female_count,
                        "revenue_per_head": revenue_per_head,
                        "annual_income": (
                            Decimal(adult_female_count) * revenue_per_head
                        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
                    }
                )

            annual_expenses = sum((row["annual"] for row in farm_expense_rows[target_id]), ZERO_DECIMAL).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            annual_net = (annual_income - annual_expenses).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            health = cls._health_summary(annual_net)
            result = {
                "id": target_id,
                "farm_id": target["farm_id"],
                "name": target["name"],
                "is_future": target["is_future"],
                "stock_rows": stock_rows,
                "expense_rows": farm_expense_rows[target_id],
                "annual_income": annual_income,
                "annual_expenses": annual_expenses,
                "annual_net": annual_net,
                "health": health,
            }
            overall_income += annual_income
            overall_farm_expenses += annual_expenses
            farm_results.append(result)

        business_expenses = sum((row["annual"] for row in business_expense_rows), ZERO_DECIMAL).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        overall_expenses = (overall_farm_expenses + business_expenses).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        overall_net = (overall_income - overall_expenses).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        overall = {
            "id": "overall",
            "name": "Overall Business",
            "expense_rows": business_expense_rows,
            "annual_income": overall_income,
            "annual_expenses": overall_expenses,
            "annual_net": overall_net,
            "health": cls._health_summary(overall_net),
        }

        return {
            "overall": overall,
            "farms": farm_results,
            "statements": [overall, *farm_results],
            "stock_detail_count": sum(len(farm["stock_rows"]) for farm in farm_results),
            "overall_forecast": cls.build_overall_forecast(scenario),
        }

    @classmethod
    def forecast_horizon_years(cls, scenario: SimulatorScenario) -> int:
        longest_loan_years = 0
        for expense in scenario.expenses:
            if expense.expense_type != "loan":
                continue
            term_years = Decimal(str(expense.remaining_term_years or 0)).to_integral_value(
                rounding=ROUND_CEILING,
            )
            longest_loan_years = max(longest_loan_years, int(term_years))
        return max(10, longest_loan_years)

    @classmethod
    def build_overall_forecast(cls, scenario: SimulatorScenario) -> dict:
        revenue_rates = cls._revenue_lookup(scenario)
        base_income = ZERO_DECIMAL
        for stock_detail in scenario.stock_details:
            revenue_per_head = revenue_rates.get((stock_detail.species, stock_detail.breed), ZERO_DECIMAL)
            base_income += Decimal(int(stock_detail.adult_female_count or 0)) * revenue_per_head
        base_income = base_income.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

        rows = []
        for year_offset in range(cls.forecast_horizon_years(scenario)):
            income = cls.inflated_amount(
                base_income,
                scenario.income_inflation_rate,
                year_offset,
            )
            expenses = sum(
                (
                    cls.expense_annual_amount_for_year(
                        expense,
                        year_offset,
                        expense_inflation_rate=scenario.expense_inflation_rate,
                    )
                    for expense in scenario.expenses
                ),
                ZERO_DECIMAL,
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            net = (income - expenses).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            rows.append(
                {
                    "year": int(scenario.projection_year) + year_offset,
                    "year_offset": year_offset,
                    "income": income,
                    "expenses": expenses,
                    "net": net,
                    "health": cls._health_summary(net),
                }
            )

        max_abs_net = max((abs(row["net"]) for row in rows), default=Decimal("1.00"))
        if max_abs_net == 0:
            max_abs_net = Decimal("1.00")
        for row in rows:
            row["bar_percent"] = float((abs(row["net"]) / max_abs_net * Decimal("100")).quantize(Decimal("0.01")))
            row["bar_class"] = "positive" if row["net"] >= 0 else "negative"

        return {
            "horizon_years": len(rows),
            "income_inflation_rate": scenario.income_inflation_rate,
            "expense_inflation_rate": scenario.expense_inflation_rate,
            "rows": rows,
        }

    @staticmethod
    def category_label(category_code: str) -> str:
        category = EXPENSE_CATEGORY_BY_CODE.get(category_code)
        return category.label if category else category_code.replace("_", " ").title()
