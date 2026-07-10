import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AnimalGroupType,
    CashTransaction,
    CashTransactionLine,
    Farm,
    LivestockTrade,
    LivestockTradeFarm,
    LivestockTradeWeight,
)
from app.services.finance_service import FinanceService
from app.services.stock_service import StockService


ZERO_DECIMAL = Decimal("0.00")
MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
WEIGHT_QUANT = Decimal("0.001")
VAT_QUANT = Decimal("0.0001")


class LivestockTradeService:
    SOURCE_TYPE = "livestock_trade"
    TRADE_TYPES = ("sale", "purchase")
    ANIMAL_MODES = {
        "lamb": {"species": "Sheep", "pricing_model": "weight"},
        "calf": {"species": "Cattle", "pricing_model": "weight"},
        "goat": {"species": "Goat", "pricing_model": "head"},
    }
    MAX_COUNTERPARTY_LENGTH = 120
    MAX_REFERENCE_LENGTH = 120
    MAX_BREED_LENGTH = 50
    MAX_HAIR_LENGTHS_LENGTH = 250
    MAX_NOTES_LENGTH = 2000

    @staticmethod
    def _getlist(source, name: str) -> list:
        if hasattr(source, "getlist"):
            return list(source.getlist(name))
        value = source.get(name) if hasattr(source, "get") else None
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    @staticmethod
    def parse_date(value, field_name: str) -> date:
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be valid (YYYY-MM-DD)") from exc

    @classmethod
    def optional_date(cls, value, field_name: str) -> date | None:
        if value in (None, ""):
            return None
        return cls.parse_date(value, field_name)

    @staticmethod
    def _normalize_decimal_text(value) -> str:
        normalized = str(value or "").strip().replace("R", "").replace("%", "").replace(" ", "")
        if "," in normalized and "." not in normalized:
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
        return normalized

    @classmethod
    def parse_decimal(
        cls,
        value,
        field_name: str,
        *,
        quant: Decimal,
        allow_zero: bool = False,
    ) -> Decimal:
        text = cls._normalize_decimal_text(value)
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid number") from exc
        if allow_zero:
            if parsed < 0:
                raise ValueError(f"{field_name} must be greater than or equal to 0")
        elif parsed <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return parsed.quantize(quant, rounding=ROUND_HALF_UP)

    @classmethod
    def parse_vat_rate(cls, value) -> Decimal:
        text = cls._normalize_decimal_text("15" if value in (None, "") else value)
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("VAT must be a valid percentage") from exc
        if parsed < 0:
            raise ValueError("VAT must be greater than or equal to 0")
        ratio = parsed if parsed <= 1 else parsed / Decimal("100")
        if ratio > 1:
            raise ValueError("VAT must be 100% or less")
        return ratio.quantize(VAT_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def parse_positive_int(cls, value, field_name: str) -> int:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a whole number") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return parsed

    @classmethod
    def parse_weights(cls, source) -> list[Decimal]:
        values = [str(value or "").strip() for value in cls._getlist(source, "weight_kg")]
        quick_entry = str(source.get("weights_csv") or "") if hasattr(source, "get") else ""
        if quick_entry.strip():
            values.extend(part.strip() for part in re.split(r"[,;\n]+", quick_entry) if part.strip())

        weights = []
        for index, raw_value in enumerate(values, start=1):
            if not raw_value:
                continue
            weights.append(
                cls.parse_decimal(
                    raw_value,
                    f"Weight {index}",
                    quant=WEIGHT_QUANT,
                    allow_zero=False,
                )
            )
        if not weights:
            raise ValueError("At least one animal weight is required")
        return weights

    @classmethod
    def normalize_trade_type(cls, value: str | None) -> str:
        trade_type = str(value or "").strip().lower()
        if trade_type not in cls.TRADE_TYPES:
            raise ValueError("Trade type must be sale or purchase")
        return trade_type

    @classmethod
    def normalize_animal_mode(cls, value: str | None) -> str:
        mode = str(value or "").strip().lower()
        if mode not in cls.ANIMAL_MODES:
            raise ValueError("Animal mode must be lamb, calf, or goat")
        return mode

    @classmethod
    def _farm_ids_from_source(cls, source) -> list[str]:
        farm_ids = []
        seen = set()
        for raw_value in cls._getlist(source, "farm_ids"):
            farm_id = str(raw_value or "").strip()
            if not farm_id or farm_id in seen:
                continue
            farm_ids.append(farm_id)
            seen.add(farm_id)
        if not farm_ids:
            raise ValueError("At least one farm is required")

        farms = Farm.query.filter(Farm.id.in_(farm_ids)).all()
        farms_by_id = {str(farm.id): farm for farm in farms}
        missing = [farm_id for farm_id in farm_ids if farm_id not in farms_by_id]
        if missing:
            raise ValueError("Selected farm is invalid")
        return farm_ids

    @classmethod
    def _group_type_from_source(cls, source, *, expected_species: str) -> AnimalGroupType:
        group_id = str(source.get("animal_group_type_id") or "").strip()
        if group_id:
            group_type = db.session.get(AnimalGroupType, group_id)
            if group_type is None:
                raise ValueError("Animal type is invalid")
            if group_type.species != expected_species:
                raise ValueError("Animal type species must match the selected livestock mode")
            return group_type

        breed = " ".join(str(source.get("breed") or "").strip().split())
        if not breed:
            raise ValueError("Breed is required")
        if len(breed) > cls.MAX_BREED_LENGTH:
            raise ValueError(f"Breed must be {cls.MAX_BREED_LENGTH} characters or fewer")
        return StockService.get_or_create_group_type(
            species=expected_species,
            breed=breed,
            sex=source.get("sex"),
            age_class=source.get("age_class"),
        )

    @classmethod
    def build_trade_payload(cls, source) -> dict:
        trade_type = cls.normalize_trade_type(source.get("trade_type"))
        animal_mode = cls.normalize_animal_mode(source.get("trade_animal_mode"))
        mode_definition = cls.ANIMAL_MODES[animal_mode]
        species = mode_definition["species"]
        pricing_model = mode_definition["pricing_model"]
        group_type = cls._group_type_from_source(source, expected_species=species)

        payload = {
            "trade_date": cls.parse_date(source.get("trade_date"), "Trade date"),
            "trade_type": trade_type,
            "animal_mode": animal_mode,
            "counterparty": FinanceService.required_text(
                source.get("counterparty"),
                "Buyer/seller",
                cls.MAX_COUNTERPARTY_LENGTH,
            ),
            "reference": FinanceService.optional_text(
                source.get("reference"),
                "Reference",
                cls.MAX_REFERENCE_LENGTH,
            ),
            "vat_rate": cls.parse_vat_rate(source.get("vat_rate")),
            "animal_group_type": group_type,
            "pricing_model": pricing_model,
            "farm_ids": cls._farm_ids_from_source(source),
            "notes": FinanceService.optional_text(
                source.get("notes"),
                "Notes",
                cls.MAX_NOTES_LENGTH,
            ),
        }

        if pricing_model == "weight":
            payload["price_per_kg"] = cls.parse_decimal(
                source.get("price_per_kg"),
                "Price per kg",
                quant=RATE_QUANT,
            )
            payload["price_per_head"] = None
            payload["goat_count"] = None
            payload["hair_lengths"] = None
            payload["weights"] = cls.parse_weights(source)
        else:
            payload["price_per_kg"] = None
            payload["weights"] = []
            payload["price_per_head"] = FinanceService.parse_money(
                source.get("price_per_head"),
                field_name="Price per goat",
            )
            payload["goat_count"] = cls.parse_positive_int(source.get("goat_count"), "Number of goats")
            payload["hair_lengths"] = FinanceService.required_text(
                source.get("hair_lengths"),
                "Hair lengths",
                cls.MAX_HAIR_LENGTHS_LENGTH,
            )
        return payload

    @classmethod
    def apply_trade_payload(cls, trade: LivestockTrade, payload: dict) -> LivestockTrade:
        if not trade.id:
            trade.id = str(uuid4())
        if trade.cash_transaction is None:
            trade.cash_transaction = CashTransaction(id=str(uuid4()))
            db.session.add(trade.cash_transaction)

        trade.trade_date = payload["trade_date"]
        trade.trade_type = payload["trade_type"]
        trade.counterparty = payload["counterparty"]
        trade.reference = payload["reference"]
        trade.vat_rate = payload["vat_rate"]
        trade.animal_group_type = payload["animal_group_type"]
        trade.animal_group_type_id = payload["animal_group_type"].id
        trade.pricing_model = payload["pricing_model"]
        trade.price_per_kg = payload["price_per_kg"]
        trade.price_per_head = payload["price_per_head"]
        trade.goat_count = payload["goat_count"]
        trade.hair_lengths = payload["hair_lengths"]
        trade.notes = payload["notes"]

        if trade.farm_links or trade.weights:
            for link in list(trade.farm_links):
                db.session.delete(link)
            for weight in list(trade.weights):
                db.session.delete(weight)
            db.session.flush()

        trade.farm_links = [
            LivestockTradeFarm(farm_id=farm_id, sort_order=index)
            for index, farm_id in enumerate(payload["farm_ids"])
        ]
        trade.weights = [
            LivestockTradeWeight(sequence=index, weight_kg=weight)
            for index, weight in enumerate(payload["weights"])
        ]
        cls.sync_cash_transaction(trade)
        return trade

    @classmethod
    def create_trade_from_form(cls, source) -> LivestockTrade:
        payload = cls.build_trade_payload(source)
        trade = LivestockTrade()
        db.session.add(trade)
        cls.apply_trade_payload(trade, payload)
        return trade

    @classmethod
    def update_trade_from_form(cls, trade: LivestockTrade, source) -> LivestockTrade:
        cls.apply_trade_payload(trade, cls.build_trade_payload(source))
        return trade

    @classmethod
    def delete_trade(cls, trade: LivestockTrade) -> None:
        transaction = trade.cash_transaction
        db.session.delete(trade)
        if transaction is not None:
            db.session.delete(transaction)

    @classmethod
    def sync_cash_transaction(cls, trade: LivestockTrade) -> CashTransaction:
        transaction = trade.cash_transaction
        if transaction is None:
            transaction = CashTransaction(id=str(uuid4()))
            db.session.add(transaction)
            trade.cash_transaction = transaction

        stats = cls.trade_statistics(trade)
        category_code, direction = cls.cash_category_and_direction(trade)
        farm_ids = [link.farm_id for link in sorted(trade.farm_links, key=lambda item: item.sort_order)]

        transaction.transaction_date = trade.trade_date
        transaction.farm_id = farm_ids[0] if len(farm_ids) == 1 else None
        transaction.reference = trade.reference
        transaction.counterparty = trade.counterparty
        transaction.description = cls.cash_transaction_description(trade, stats=stats)
        transaction.lines = [
            CashTransactionLine(
                category_code=category_code,
                direction=direction,
                species_scope=trade.animal_group_type.species,
                amount=stats["gross_total"],
                source_type=cls.SOURCE_TYPE,
                source_id=str(trade.id),
            )
        ]
        return transaction

    @classmethod
    def cash_category_and_direction(cls, trade: LivestockTrade) -> tuple[str, str]:
        if trade.trade_type == "purchase":
            return "purchases", "outflow"
        species = trade.animal_group_type.species
        if species == "Sheep":
            return "lamb_sales", "inflow"
        if species == "Cattle":
            return "calf_sales", "inflow"
        return "goat_sales", "inflow"

    @classmethod
    def trade_statistics(cls, trade: LivestockTrade) -> dict:
        vat_rate = Decimal(trade.vat_rate or 0)
        if trade.pricing_model == "weight":
            weights = [Decimal(weight.weight_kg or 0) for weight in trade.weights]
            count = len(weights)
            total_weight = sum(weights, Decimal("0.000")).quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)
            base_total = (Decimal(trade.price_per_kg or 0) * total_weight).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            unit_price = Decimal(trade.price_per_kg or 0).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
            gross_unit_price = (unit_price * (Decimal("1.0000") + vat_rate)).quantize(
                RATE_QUANT,
                rounding=ROUND_HALF_UP,
            )
        else:
            count = int(trade.goat_count or 0)
            total_weight = None
            base_total = (Decimal(trade.price_per_head or 0) * Decimal(count)).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            unit_price = Decimal(trade.price_per_head or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
            gross_unit_price = (unit_price * (Decimal("1.0000") + vat_rate)).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )

        vat_amount = (base_total * vat_rate).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        gross_total = (base_total + vat_amount).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_money = base_total
        average_price_per_animal = None
        if count > 0:
            average_price_per_animal = (total_money / Decimal(count)).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )

        average_weight = None
        if total_weight is not None and count > 0:
            average_weight = (total_weight / Decimal(count)).quantize(
                WEIGHT_QUANT,
                rounding=ROUND_HALF_UP,
            )

        return {
            "count": count,
            "total_weight": total_weight,
            "average_weight": average_weight,
            "unit_price": unit_price,
            "gross_unit_price": gross_unit_price,
            "base_total": base_total,
            "vat_amount": vat_amount,
            "gross_total": gross_total,
            "total_money": total_money,
            "average_price_per_animal": average_price_per_animal,
        }

    @classmethod
    def cash_transaction_description(cls, trade: LivestockTrade, *, stats: dict | None = None) -> str:
        stats = stats or cls.trade_statistics(trade)
        action = "Sale" if trade.trade_type == "sale" else "Purchase"
        animal_name = cls.mode_label_for_species(trade.animal_group_type.species).lower()
        return f"{action} of {stats['count']} {animal_name} - {trade.counterparty}"

    @classmethod
    def managed_trade_for_transaction(cls, transaction: CashTransaction) -> LivestockTrade | None:
        return LivestockTrade.query.filter_by(cash_transaction_id=transaction.id).first()

    @classmethod
    def managed_trade_map(cls, transaction_ids: list[str]) -> dict[str, LivestockTrade]:
        if not transaction_ids:
            return {}
        trades = LivestockTrade.query.filter(LivestockTrade.cash_transaction_id.in_(transaction_ids)).all()
        return {str(trade.cash_transaction_id): trade for trade in trades}

    @staticmethod
    def mode_label_for_species(species: str) -> str:
        if species == "Sheep":
            return "Lambs"
        if species == "Cattle":
            return "Calves"
        return "Goats"

    @staticmethod
    def trade_type_label(trade_type: str) -> str:
        return "Sale" if trade_type == "sale" else "Purchase"

    @staticmethod
    def counterparty_role(trade_type: str) -> str:
        return "Buyer" if trade_type == "sale" else "Seller"

    @staticmethod
    def animal_group_label(group_type: AnimalGroupType) -> str:
        return f"{group_type.species} {group_type.breed} {group_type.sex} {group_type.age_class}"

    @staticmethod
    def farm_names(trade: LivestockTrade) -> list[str]:
        links = sorted(trade.farm_links, key=lambda link: link.sort_order)
        return [link.farm.name for link in links if link.farm is not None]

    @classmethod
    def serialize_trade(cls, trade: LivestockTrade) -> dict:
        stats = cls.trade_statistics(trade)
        group_type = trade.animal_group_type
        return {
            "id": str(trade.id),
            "trade_date": trade.trade_date.isoformat(),
            "trade_type": trade.trade_type,
            "trade_type_label": cls.trade_type_label(trade.trade_type),
            "counterparty_role": cls.counterparty_role(trade.trade_type),
            "counterparty": trade.counterparty,
            "reference": trade.reference,
            "vat_rate": Decimal(trade.vat_rate or 0),
            "vat_percent": (Decimal(trade.vat_rate or 0) * Decimal("100")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            "animal_group_type_id": str(trade.animal_group_type_id),
            "animal_group_type": {
                "id": str(group_type.id),
                "species": group_type.species,
                "breed": group_type.breed,
                "sex": group_type.sex,
                "age_class": group_type.age_class,
                "label": cls.animal_group_label(group_type),
            },
            "animal_mode": cls.mode_value_for_species(group_type.species),
            "animal_mode_label": cls.mode_label_for_species(group_type.species),
            "pricing_model": trade.pricing_model,
            "price_per_kg": Decimal(trade.price_per_kg or 0) if trade.price_per_kg is not None else None,
            "price_per_head": (
                Decimal(trade.price_per_head or 0) if trade.price_per_head is not None else None
            ),
            "goat_count": trade.goat_count,
            "hair_lengths": trade.hair_lengths,
            "notes": trade.notes,
            "farm_ids": [str(link.farm_id) for link in sorted(trade.farm_links, key=lambda link: link.sort_order)],
            "farm_names": cls.farm_names(trade),
            "weights": [Decimal(weight.weight_kg or 0) for weight in trade.weights],
            "stats": stats,
            "category_code": cls.cash_category_and_direction(trade)[0],
            "cash_transaction_id": str(trade.cash_transaction_id),
        }

    @staticmethod
    def mode_value_for_species(species: str) -> str:
        if species == "Sheep":
            return "lamb"
        if species == "Cattle":
            return "calf"
        return "goat"

    @classmethod
    def trade_query(cls):
        return LivestockTrade.query.options(
            selectinload(LivestockTrade.animal_group_type),
            selectinload(LivestockTrade.farm_links).selectinload(LivestockTradeFarm.farm),
            selectinload(LivestockTrade.weights),
            selectinload(LivestockTrade.cash_transaction).selectinload(CashTransaction.lines),
        )

    @classmethod
    def trades_for_period(
        cls,
        *,
        start_date: date,
        end_date: date,
        trade_type: str | None = None,
        species: str | None = None,
        farm_id: str | None = None,
    ) -> list[LivestockTrade]:
        query = cls.trade_query().join(
            AnimalGroupType,
            LivestockTrade.animal_group_type_id == AnimalGroupType.id,
        )
        query = query.filter(LivestockTrade.trade_date >= start_date, LivestockTrade.trade_date <= end_date)
        if trade_type:
            query = query.filter(LivestockTrade.trade_type == cls.normalize_trade_type(trade_type))
        if species:
            query = query.filter(AnimalGroupType.species == StockService.normalize_species(species))
        if farm_id:
            farm = db.session.get(Farm, str(farm_id))
            if farm is None:
                raise ValueError("Selected farm is invalid")
            query = query.filter(LivestockTrade.farm_links.any(LivestockTradeFarm.farm_id == str(farm_id)))
        return query.order_by(LivestockTrade.trade_date.asc(), LivestockTrade.created_at.asc()).all()

    @classmethod
    def build_period_report(
        cls,
        *,
        start_date: date,
        end_date: date,
        trade_type: str | None = None,
        species: str | None = None,
        farm_id: str | None = None,
    ) -> dict:
        trades = cls.trades_for_period(
            start_date=start_date,
            end_date=end_date,
            trade_type=trade_type,
            species=species,
            farm_id=farm_id,
        )
        rows = [cls.serialize_trade(trade) for trade in trades]

        total_count = sum(row["stats"]["count"] for row in rows)
        total_weight = sum(
            (row["stats"]["total_weight"] or Decimal("0.000") for row in rows),
            Decimal("0.000"),
        ).quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)
        weighted_count = sum(row["stats"]["count"] for row in rows if row["stats"]["total_weight"] is not None)
        total_sales = sum(
            (row["stats"]["total_money"] for row in rows if row["trade_type"] == "sale"),
            ZERO_DECIMAL,
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_purchases = sum(
            (row["stats"]["total_money"] for row in rows if row["trade_type"] == "purchase"),
            ZERO_DECIMAL,
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_money = sum((row["stats"]["total_money"] for row in rows), ZERO_DECIMAL).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )

        average_weight = None
        if weighted_count > 0:
            average_weight = (total_weight / Decimal(weighted_count)).quantize(
                WEIGHT_QUANT,
                rounding=ROUND_HALF_UP,
            )
        average_price_per_animal = None
        if total_count > 0:
            average_price_per_animal = (total_money / Decimal(total_count)).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
        average_price_per_kg = None
        if total_weight > 0:
            weighted_money = sum(
                (
                    row["stats"]["total_money"]
                    for row in rows
                    if row["stats"]["total_weight"] is not None
                ),
                ZERO_DECIMAL,
            )
            average_price_per_kg = (weighted_money / total_weight).quantize(
                RATE_QUANT,
                rounding=ROUND_HALF_UP,
            )

        return {
            "rows": rows,
            "summary": {
                "trade_count": len(rows),
                "animal_count": total_count,
                "total_weight": total_weight,
                "average_weight": average_weight,
                "average_price_per_animal": average_price_per_animal,
                "average_price_per_kg": average_price_per_kg,
                "total_sales_income": total_sales,
                "total_purchase_spend": total_purchases,
                "net_sales_minus_purchases": (total_sales - total_purchases).quantize(
                    MONEY_QUANT,
                    rounding=ROUND_HALF_UP,
                ),
            },
            "chart_payload": cls.build_chart_payload(rows),
        }

    @classmethod
    def build_chart_payload(cls, rows: list[dict]) -> dict:
        labels = [
            f"{row['trade_date']} {row['trade_type_label']} {row['animal_mode_label']}"
            for row in rows
        ]
        species_options = ("Sheep", "Cattle", "Goat")

        def species_values(species: str, value_getter) -> list[float | None]:
            values = []
            for row in rows:
                if row["animal_group_type"]["species"] != species:
                    values.append(None)
                    continue
                value = value_getter(row)
                values.append(float(value) if value is not None else None)
            return values

        def price_value(row):
            return row["stats"]["unit_price"]

        def average_weight_value(row):
            return row["stats"]["average_weight"]

        price_labels = {
            "Sheep": "Sheep R/kg",
            "Cattle": "Cattle R/kg",
            "Goat": "Goat R/head",
        }

        panels = []
        if rows:
            panels = [
                {
                    "title": "VAT-Exclusive Prices",
                    "value_format": "money",
                    "y_axis_label": "Price",
                    "datasets": [
                        {
                            "label": price_labels[species],
                            "species": species,
                            "values": species_values(species, price_value),
                        }
                        for species in species_options
                    ],
                },
                {
                    "title": "Animals Per Trade",
                    "value_format": "count",
                    "y_axis_label": "Animals",
                    "datasets": [
                        {
                            "label": species,
                            "species": species,
                            "values": species_values(species, lambda row: row["stats"]["count"]),
                        }
                        for species in species_options
                    ],
                },
                {
                    "title": "Average Weight",
                    "value_format": "kg",
                    "y_axis_label": "Kg",
                    "datasets": [
                        {
                            "label": species,
                            "species": species,
                            "values": species_values(species, average_weight_value),
                        }
                        for species in species_options
                    ],
                },
                {
                    "title": "Total Excl VAT",
                    "value_format": "money",
                    "y_axis_label": "Money",
                    "datasets": [
                        {
                            "label": species,
                            "species": species,
                            "values": species_values(species, lambda row: row["stats"]["total_money"]),
                        }
                        for species in species_options
                    ],
                },
            ]
        return {"labels": labels, "panels": panels}

    @classmethod
    def available_years(cls, selected_year: int | None = None) -> list[int]:
        values = (
            db.session.query(func.strftime("%Y", LivestockTrade.trade_date))
            .filter(LivestockTrade.trade_date.isnot(None))
            .distinct()
            .all()
        )
        years = {int(value[0]) for value in values if value[0]}
        years.add(selected_year or date.today().year)
        years.add(date.today().year)
        return sorted(years, reverse=True)
