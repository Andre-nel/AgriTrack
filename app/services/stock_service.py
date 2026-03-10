from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, StockLedgerEntry
from app.models.stock_ledger import StockEventType
from app.services.validation_service import ValidationService

IN_EVENTS = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}


class StockService:
    SPECIES_MAP = {
        "cattle": "Cattle",
        "sheep": "Sheep",
        "goat": "Goat",
    }
    AGE_CLASS_OPTIONS = {
        "Cattle": ("calf", "young", "adult", "old"),
        "Sheep": ("lamb", "young", "adult", "old"),
        "Goat": ("kid", "young", "adult", "old"),
    }
    SEX_OPTIONS = {
        "Cattle": ("mixed", "cow", "bul", "ox"),
        "Sheep": ("mixed", "ewe", "ram", "wether"),
        "Goat": ("mixed", "ewe", "ram", "wether"),
    }

    @classmethod
    def normalize_species(cls, species: str) -> str:
        key = (species or "").strip().lower()
        normalized = cls.SPECIES_MAP.get(key)
        if not normalized:
            allowed = ", ".join(cls.SPECIES_MAP.values())
            raise ValueError(f"Species must be one of: {allowed}")
        return normalized

    @classmethod
    def normalize_age_class(cls, species: str, age_class: str) -> str:
        value = (age_class or "").strip().lower()
        allowed = cls.AGE_CLASS_OPTIONS[species]
        if value not in allowed:
            allowed_text = ", ".join(allowed)
            raise ValueError(f"Age class for {species} must be one of: {allowed_text}")
        return value

    @classmethod
    def normalize_sex(cls, species: str, sex: str) -> str:
        value = (sex or "").strip().lower()
        if species == "Cattle" and value == "bull":
            value = "bul"
        allowed = cls.SEX_OPTIONS[species]
        if value not in allowed:
            allowed_text = ", ".join(allowed)
            raise ValueError(f"Sex for {species} must be one of: {allowed_text}")
        return value

    @staticmethod
    def get_or_create_group_type(species: str, breed: str, sex: str, age_class: str) -> AnimalGroupType:
        species = StockService.normalize_species(species)
        sex = StockService.normalize_sex(species, sex)
        age_class = StockService.normalize_age_class(species, age_class)
        group_type = AnimalGroupType.query.filter_by(
            species=species,
            breed=breed,
            sex=sex,
            age_class=age_class,
        ).first()
        if group_type:
            return group_type

        group_type = AnimalGroupType(species=species, breed=breed, sex=sex, age_class=age_class)
        db.session.add(group_type)
        db.session.flush()
        return group_type

    @staticmethod
    def adjust_stock(
        mob_id: str,
        farm_id: str,
        animal_group_type_id: str,
        event_type: StockEventType,
        quantity: int,
        note: str | None = None,
        event_time: datetime | None = None,
        sync_grazing_history: bool = True,
    ) -> StockLedgerEntry:
        if event_type == StockEventType.count:
            raise ValueError("Count events must be resolved to adjustment_in or missing before posting.")

        ValidationService.validate_positive_int(quantity, "quantity")

        ledger = StockLedgerEntry(
            mob_id=mob_id,
            farm_id=farm_id,
            animal_group_type_id=animal_group_type_id,
            event_type=event_type,
            quantity=quantity,
            event_time=event_time or datetime.now(timezone.utc),
            note=note,
        )
        db.session.add(ledger)

        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id, animal_group_type_id=animal_group_type_id
        ).first()
        if not balance:
            balance = AnimalGroupBalance(
                mob_id=mob_id,
                animal_group_type_id=animal_group_type_id,
                head_count=0,
            )
            db.session.add(balance)

        delta = quantity if event_type in IN_EVENTS else -quantity
        next_balance = balance.head_count + delta
        if next_balance < 0:
            raise ValueError("Stock balance cannot go negative")

        if next_balance == 0:
            db.session.delete(balance)
        else:
            balance.head_count = next_balance

        if sync_grazing_history:
            from app.services.grazing_history_service import GrazingHistoryService

            GrazingHistoryService.sync_live_history_for_mob(mob_id, effective_at=ledger.event_time)
        return ledger
