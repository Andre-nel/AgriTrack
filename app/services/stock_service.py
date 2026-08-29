from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalCohort, AnimalGroupBalance, AnimalGroupType, Mob, StockLedgerEntry
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
        cohort_id: str | None = None,
        note: str | None = None,
        event_time: datetime | None = None,
        sync_grazing_history: bool = True,
        sync_allocation_distribution: bool = True,
        allocation_paddock_id: str | None = None,
    ) -> StockLedgerEntry:
        if event_type == StockEventType.count:
            raise ValueError("Count events must be resolved to adjustment_in or missing before posting.")

        ValidationService.validate_positive_int(quantity, "quantity")
        posted_at = event_time or datetime.now(timezone.utc)

        from app.services.cohort_service import CohortService

        balance = CohortService.balance_for_selector(
            mob_id=str(mob_id),
            animal_group_type_id=str(animal_group_type_id),
            cohort_id=str(cohort_id) if cohort_id else None,
        )
        cohort = None
        if balance is not None:
            cohort = CohortService.ensure_balance_cohort(balance)
        elif cohort_id:
            cohort = db.session.get(AnimalCohort, cohort_id)
            if cohort is None:
                raise ValueError("Selected animal cohort is invalid")
            if str(cohort.animal_group_type_id) != str(animal_group_type_id):
                raise ValueError("Selected cohort does not match the animal group type")
        elif event_type not in IN_EVENTS:
            raise ValueError("Stock balance is not available for this animal type")
        else:
            origin_by_event = {
                StockEventType.birth: "birth",
                StockEventType.purchase: "purchase",
            }
            cohort = CohortService.create_cohort(
                animal_group_type_id=str(animal_group_type_id),
                origin_farm_id=str(farm_id),
                origin=origin_by_event.get(event_type, "manual"),
            )

        ledger = StockLedgerEntry(
            mob_id=mob_id,
            farm_id=farm_id,
            animal_group_type_id=animal_group_type_id,
            cohort_id=cohort.id,
            event_type=event_type,
            quantity=quantity,
            event_time=posted_at,
            note=note,
        )
        db.session.add(ledger)

        if not balance:
            balance = AnimalGroupBalance(
                mob_id=mob_id,
                animal_group_type_id=animal_group_type_id,
                cohort_id=cohort.id,
                head_count=0,
            )
            db.session.add(balance)

        current_group_total = sum(
            int(row.head_count or 0)
            for row in AnimalGroupBalance.query.filter_by(
                mob_id=mob_id,
                animal_group_type_id=animal_group_type_id,
            ).all()
        )
        delta = quantity if event_type in IN_EVENTS else -quantity
        next_balance = balance.head_count + delta
        if next_balance < 0:
            raise ValueError("Stock balance cannot go negative")

        if sync_allocation_distribution:
            from app.services.allocation_distribution_service import AllocationDistributionService

            AllocationDistributionService.apply_stock_delta(
                mob_id,
                animal_group_type_id=animal_group_type_id,
                delta=delta,
                final_head_count=current_group_total + delta,
                paddock_id=allocation_paddock_id,
            )

        if next_balance == 0:
            db.session.delete(balance)
        else:
            balance.head_count = next_balance

        db.session.flush()
        mob_obj = db.session.get(Mob, mob_id)
        if mob_obj is not None:
            db.session.expire(mob_obj, ["balances"])

        if sync_grazing_history:
            from app.services.grazing_history_service import GrazingHistoryService

            GrazingHistoryService.sync_live_history_for_mob(mob_id, effective_at=posted_at)
        return ledger
