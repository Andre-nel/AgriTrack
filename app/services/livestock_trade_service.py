import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    CashTransaction, CashTransactionLine, Farm, LivestockTrade,
    LivestockTradeFarm, LivestockTradeGroup, LivestockTradeWeight,
)
from app.services.livestock_trade_attachment_service import LivestockTradeAttachmentService
from app.services.livestock_trade_calculations import payload_statistics
from app.services.livestock_trade_payload import (
    MONEY_QUANT, RATE_QUANT, WEIGHT_QUANT, document_from_form, getlist,
    normalize_document, public_sex,
)
from app.services.stock_service import StockService

ZERO_DECIMAL = Decimal("0.00")


class LivestockTradeService:
    SOURCE_TYPE = "livestock_trade"
    TRADE_TYPES = ("sale", "purchase")

    @staticmethod
    def parse_date(value, field_name):
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be valid (YYYY-MM-DD)") from exc

    @classmethod
    def optional_date(cls, value, field_name):
        return cls.parse_date(value, field_name) if value else None

    @staticmethod
    def normalize_trade_type(value):
        value = str(value or "").lower().strip()
        if value not in ("sale", "purchase"):
            raise ValueError("Trade type must be sale or purchase")
        return value

    @classmethod
    def build_trade_payload(cls, source):
        payload = normalize_document(document_from_form(source), getlist(source, "farm_ids"), require_import_id=False)
        payload_statistics(payload)
        return payload

    @staticmethod
    def payload_from_trade(trade):
        return {
            "trade_date": trade.trade_date, "trade_type": trade.trade_type,
            "species": trade.species, "pricing_model": trade.pricing_model,
            "counterparty": trade.counterparty, "reference": trade.reference,
            "vat_rate": Decimal(trade.vat_rate), "notes": trade.notes,
            "farm_ids": [link.farm_id for link in trade.farm_links],
            "groups": [{
                "label": group.label, "notes": group.notes,
                "breed": group.animal_group_type.breed,
                "sex": group.animal_group_type.sex,
                "age_class": group.animal_group_type.age_class,
                "weights": [Decimal(weight.weight_kg) for weight in group.weights],
                "price_per_kg": group.price_per_kg,
                "price_per_head": group.price_per_head,
                "head_count": group.head_count, "hair_lengths": group.hair_lengths,
            } for group in trade.groups],
        }

    @classmethod
    def document_for_trade(cls, trade):
        payload = cls.payload_from_trade(trade)
        result = {key: payload[key] for key in ("trade_type", "counterparty", "species", "pricing_model", "reference", "notes")}
        result.update(schema_version=1, import_id=trade.import_id, date=trade.trade_date.isoformat(),
                      vat_percent=str(Decimal(trade.vat_rate) * 100), groups=[])
        for group in payload["groups"]:
            item = {key: group[key] for key in ("label", "breed", "age_class", "notes", "hair_lengths")}
            item["sex"] = public_sex(trade.species, group["sex"])
            if trade.pricing_model == "weight":
                item.update(weights_kg=[str(value) for value in group["weights"]], price_per_kg=str(group["price_per_kg"]))
            else:
                item.update(head_count=group["head_count"], price_per_head=str(group["price_per_head"]))
            result["groups"].append(item)
        return result

    @classmethod
    def apply_trade_payload(cls, trade, payload):
        # Callers own commit/rollback; validation finishes before any write.
        payload_statistics(payload)
        animal_types = [StockService.get_or_create_group_type(
            species=payload["species"], breed=source["breed"], sex=source["sex"], age_class=source["age_class"],
        ) for source in payload["groups"]]
        if not trade.id:
            trade.id = str(uuid4())
        if trade.cash_transaction is None:
            trade.cash_transaction = CashTransaction(id=str(uuid4()))
            db.session.add(trade.cash_transaction)
        for field in ("trade_date", "trade_type", "counterparty", "reference", "vat_rate", "species", "pricing_model", "notes"):
            setattr(trade, field, payload[field])
        if trade.groups or trade.farm_links:
            trade.groups.clear()
            trade.farm_links.clear()
            db.session.flush()
        trade.farm_links = [LivestockTradeFarm(farm_id=farm_id, sort_order=index) for index, farm_id in enumerate(payload["farm_ids"])]
        for index, (source, animal_type) in enumerate(zip(payload["groups"], animal_types)):
            group = LivestockTradeGroup(
                sort_order=index, animal_group_type=animal_type,
                **{key: source[key] for key in ("label", "notes", "price_per_kg", "price_per_head", "head_count", "hair_lengths")},
            )
            group.weights = [LivestockTradeWeight(sequence=i, weight_kg=weight) for i, weight in enumerate(source["weights"])]
            trade.groups.append(group)
        cls.sync_cash_transaction(trade)
        return trade

    @classmethod
    def create_trade_from_form(cls, source):
        payload = cls.build_trade_payload(source)
        trade = LivestockTrade()
        # Build the complete aggregate before queries can trigger an autoflush.
        with db.session.no_autoflush:
            cls.apply_trade_payload(trade, payload)
        db.session.add(trade)
        return trade

    @classmethod
    def update_trade_from_form(cls, trade, source):
        if len(trade.groups) > 1 and "group_count" not in source:
            raise ValueError("Use the grouped editor to update a multi-group trade")
        return cls.apply_trade_payload(trade, cls.build_trade_payload(source))

    @staticmethod
    def payload_hash(payload, attachments=()):
        canonical = {key: value for key, value in payload.items() if key != "import_id"}
        if attachments:
            canonical["reference_images"] = LivestockTradeAttachmentService.fingerprints(attachments)
        encoded = json.dumps(canonical, default=str, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def find_import(cls, import_id, fingerprint):
        existing = cls.trade_query().filter_by(import_id=import_id).first()
        if existing is not None and existing.import_payload_hash != fingerprint:
            raise ValueError("This import_id already belongs to a different completed payload. Edit the existing trade instead.")
        return existing

    @classmethod
    def import_document(cls, document, farm_ids, *, attachments=()):
        """Atomically commit one import; the only service entry point owning commit.

        The DB unique key arbitrates concurrent retries. On a uniqueness race the
        entire losing transaction (including animal types and cashflow) rolls back.
        """
        payload = normalize_document(document, farm_ids)
        payload_statistics(payload)
        attachments = [LivestockTradeAttachmentService.describe_token(item["token"], item["owner"]) for item in attachments]
        fingerprint = cls.payload_hash(payload, attachments)
        existing = cls.find_import(payload["import_id"], fingerprint)
        if existing is not None:
            return existing, False
        created_images = []
        try:
            trade = LivestockTrade(import_id=payload["import_id"], import_payload_hash=fingerprint)
            with db.session.no_autoflush:
                cls.apply_trade_payload(trade, payload)
                created_images = LivestockTradeAttachmentService.copy_to_trade(trade, attachments)
            db.session.add(trade)
            db.session.commit()
            return trade, True
        except IntegrityError:
            db.session.rollback()
            LivestockTradeAttachmentService.delete_files(created_images)
            existing = cls.find_import(payload["import_id"], fingerprint)
            if existing is not None:
                return existing, False
            raise
        except Exception:
            db.session.rollback()
            LivestockTradeAttachmentService.delete_files(created_images)
            raise

    @classmethod
    def delete_trade(cls, trade):
        transaction = trade.cash_transaction
        db.session.delete(trade)
        if transaction is not None:
            db.session.delete(transaction)

    @classmethod
    def sync_cash_transaction(cls, trade):
        transaction = trade.cash_transaction
        stats = cls.trade_statistics(trade)
        category, direction = cls.cash_category_and_direction(trade)
        farm_ids = [link.farm_id for link in trade.farm_links]
        transaction.transaction_date = trade.trade_date
        transaction.farm_id = farm_ids[0] if len(farm_ids) == 1 else None
        transaction.reference = trade.reference
        transaction.counterparty = trade.counterparty
        transaction.description = cls.cash_transaction_description(trade, stats=stats)
        transaction.lines = [CashTransactionLine(
            category_code=category, direction=direction, species_scope=trade.species,
            amount=stats["gross_total"], source_type=cls.SOURCE_TYPE, source_id=trade.id,
        )]
        return transaction

    @staticmethod
    def cash_category_and_direction(trade):
        if trade.trade_type == "purchase":
            return "purchases", "outflow"
        return {"Cattle": "calf_sales", "Sheep": "lamb_sales", "Goat": "goat_sales"}[trade.species], "inflow"

    @classmethod
    def trade_statistics(cls, trade):
        return payload_statistics(cls.payload_from_trade(trade))["stats"]

    @classmethod
    def cash_transaction_description(cls, trade, *, stats=None):
        stats = stats or cls.trade_statistics(trade)
        return f"{cls.trade_type_label(trade.trade_type)} of {stats['count']} {cls.mode_label_for_species(trade.species).lower()} - {trade.counterparty}"

    @staticmethod
    def managed_trade_for_transaction(transaction):
        return LivestockTrade.query.filter_by(cash_transaction_id=transaction.id).first()

    @staticmethod
    def managed_trade_map(transaction_ids):
        if not transaction_ids:
            return {}
        return {str(trade.cash_transaction_id): trade for trade in LivestockTrade.query.filter(LivestockTrade.cash_transaction_id.in_(transaction_ids)).all()}

    @staticmethod
    def mode_label_for_species(species):
        return {"Sheep": "Lambs", "Cattle": "Calves", "Goat": "Goats"}[species]

    @staticmethod
    def mode_value_for_species(species):
        return {"Sheep": "lamb", "Cattle": "calf", "Goat": "goat"}[species]

    @staticmethod
    def trade_type_label(trade_type):
        return "Sale" if trade_type == "sale" else "Purchase"

    @staticmethod
    def counterparty_role(trade_type):
        return "Buyer" if trade_type == "sale" else "Seller"

    @staticmethod
    def animal_group_label(group_type):
        return f"{group_type.species} {group_type.breed} {public_sex(group_type.species, group_type.sex)} {group_type.age_class}"

    @staticmethod
    def farm_names(trade):
        return [link.farm.name for link in trade.farm_links if link.farm is not None]

    @classmethod
    def serialize_trade(cls, trade):
        payload = cls.payload_from_trade(trade)
        calculations = payload_statistics(payload)
        groups = []
        for group, values, stats in zip(trade.groups, payload["groups"], calculations["groups"]):
            groups.append({**values, "id": group.id, "animal_group_type_id": group.animal_group_type_id,
                           "animal_type_label": cls.animal_group_label(group.animal_group_type),
                           "sex_label": public_sex(trade.species, values["sex"]), "stats": stats})
        return {
            **payload, "id": trade.id, "trade_date": trade.trade_date.isoformat(),
            "trade_type_label": cls.trade_type_label(trade.trade_type),
            "counterparty_role": cls.counterparty_role(trade.trade_type),
            "vat_percent": Decimal(trade.vat_rate) * 100,
            "animal_mode": cls.mode_value_for_species(trade.species),
            "animal_mode_label": cls.mode_label_for_species(trade.species),
            "animal_label": "; ".join(group["label"] or group["animal_type_label"] for group in groups),
            "farm_names": cls.farm_names(trade), "groups": groups,
            "stats": calculations["stats"],
            "stat_rows": [(group["label"] or group["animal_type_label"], group["stats"]) for group in groups]
                + [(f"Sex: {public_sex(trade.species, key)}", value) for key, value in calculations["sex_stats"].items()]
                + [("Combined trade", calculations["stats"])],
            "sex_stats": {public_sex(trade.species, key): value for key, value in calculations["sex_stats"].items()},
            "category_code": cls.cash_category_and_direction(trade)[0],
            "cash_transaction_id": trade.cash_transaction_id,
        }

    @classmethod
    def trade_query(cls):
        return LivestockTrade.query.options(
            selectinload(LivestockTrade.groups).selectinload(LivestockTradeGroup.animal_group_type),
            selectinload(LivestockTrade.groups).selectinload(LivestockTradeGroup.weights),
            selectinload(LivestockTrade.farm_links).selectinload(LivestockTradeFarm.farm),
            selectinload(LivestockTrade.cash_transaction).selectinload(CashTransaction.lines),
        )

    @classmethod
    def trades_for_period(cls, *, start_date, end_date, trade_type=None, species=None, farm_id=None):
        query = cls.trade_query().filter(LivestockTrade.trade_date >= start_date, LivestockTrade.trade_date <= end_date)
        if trade_type:
            query = query.filter(LivestockTrade.trade_type == cls.normalize_trade_type(trade_type))
        if species:
            query = query.filter(LivestockTrade.species == StockService.normalize_species(species))
        if farm_id:
            if db.session.get(Farm, str(farm_id)) is None:
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
                if row["species"] != species:
                    values.append(None)
                    continue
                value = value_getter(row)
                values.append(float(value) if value is not None else None)
            return values

        def average_weight_value(row):
            return row["stats"]["average_weight"]

        # Never plot R/head and R/kg as a single series for the same species.
        price_datasets = []
        for species in species_options:
            methods = {row["pricing_model"] for row in rows if row["species"] == species}
            methods = methods or {"head" if species == "Goat" else "weight"}
            for method in sorted(methods, reverse=True):
                price_datasets.append({
                    "label": f"{species} R/{'kg' if method == 'weight' else 'head'}",
                    "species": species,
                    "values": [float(row["stats"]["unit_price"]) if row["species"] == species and row["pricing_model"] == method else None for row in rows],
                })

        panels = []
        if rows:
            panels = [
                {
                    "title": "VAT-Exclusive Prices",
                    "value_format": "money",
                    "y_axis_label": "Price",
                    "datasets": price_datasets,
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
