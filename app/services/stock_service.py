from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalCohort, AnimalGroupBalance, AnimalGroupType, Mob, StockLedgerEntry
from app.models.stock_ledger import StockEventType
from app.services.validation_service import ValidationService
from app.services.stock_groups import apportion_counts, summarize_balances

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

    @staticmethod
    def group_balances(mob_id: str, animal_group_type_id: str) -> list[AnimalGroupBalance]:
        return (
            AnimalGroupBalance.query.filter_by(
                mob_id=str(mob_id), animal_group_type_id=str(animal_group_type_id)
            )
            .filter(AnimalGroupBalance.head_count > 0)
            .order_by(AnimalGroupBalance.id)
            .all()
        )

    @classmethod
    def group_head_count(cls, mob_id: str, animal_group_type_id: str) -> int:
        return sum(int(row.head_count) for row in cls.group_balances(mob_id, animal_group_type_id))

    @staticmethod
    def group_summary_rows(mob: Mob) -> list[dict]:
        return summarize_balances(mob.balances)

    @staticmethod
    def proportional_note(note: str | None) -> str:
        annotation = "Cohort quantities allocated proportionally."
        text = (note or "").strip()
        return f"{text} {annotation}" if text else annotation

    @classmethod
    def adjust_group_stock(
        cls,
        mob_id: str,
        farm_id: str,
        animal_group_type_id: str,
        event_type: StockEventType,
        quantity: int,
        note: str | None = None,
        event_time: datetime | None = None,
        sync_grazing_history: bool = True,
        sync_allocation_distribution: bool = True,
        allocation_paddock_id: str | None = None,
    ) -> list[StockLedgerEntry]:
        """Post one animal-type operation; callers own the transaction boundary."""
        from app.services.allocation_distribution_service import AllocationDistributionService
        from app.services.cohort_service import CohortService
        from app.services.grazing_history_service import GrazingHistoryService

        event_type = StockEventType(event_type)
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError("quantity must be a whole number")
        mob = db.session.get(Mob, str(mob_id))
        if mob is None or str(mob.farm_id) != str(farm_id) or mob.status != "active":
            raise ValueError("Selected mob is not active on this farm")
        if db.session.get(AnimalGroupType, str(animal_group_type_id)) is None:
            raise ValueError("Selected animal group type is invalid")
        balances = cls.group_balances(mob_id, animal_group_type_id)
        current = sum(int(row.head_count) for row in balances)
        if event_type == StockEventType.count:
            if quantity < 0:
                raise ValueError("quantity must be greater than or equal to 0")
            delta = quantity - current
            if not delta:
                return []
            event_type = StockEventType.adjustment_in if delta > 0 else StockEventType.missing
            quantity = abs(delta)
        else:
            ValidationService.validate_positive_int(quantity, "quantity")
            delta = quantity if event_type in IN_EVENTS else -quantity
        if current + delta < 0:
            raise ValueError("Stock balance cannot go negative")
        posted_at = event_time or datetime.now(timezone.utc)

        if sync_allocation_distribution:
            AllocationDistributionService.apply_stock_delta(
                mob_id, animal_group_type_id=animal_group_type_id, delta=delta,
                final_head_count=current + delta, paddock_id=allocation_paddock_id,
                effective_at=posted_at,
            )
        if delta > 0:
            # New arrivals and count surpluses have no recorded reproductive identity.
            cohort = CohortService.create_cohort(
                animal_group_type_id=str(animal_group_type_id), origin_farm_id=str(farm_id),
                origin={StockEventType.birth: "birth", StockEventType.purchase: "purchase"}.get(
                    event_type, "manual"
                ),
            )
            postings = [(cohort.id, quantity)]
        else:
            shares = apportion_counts(quantity, [(str(row.id), int(row.head_count)) for row in balances])
            if len(balances) > 1:
                note = cls.proportional_note(note)
            postings = [
                (CohortService.ensure_balance_cohort(row).id, shares[str(row.id)])
                for row in balances if shares[str(row.id)] > 0
            ]
        entries = [
            cls.adjust_stock(
                mob_id=mob_id, farm_id=farm_id, animal_group_type_id=animal_group_type_id,
                cohort_id=cohort_id, event_type=event_type, quantity=amount,
                note=note, event_time=posted_at, sync_grazing_history=False,
                sync_allocation_distribution=False,
            )
            for cohort_id, amount in postings
        ]
        if sync_grazing_history:
            GrazingHistoryService.sync_live_history_for_mob(mob_id, effective_at=posted_at)
        return entries

    @classmethod
    def reclassify_group_stock(
        cls, mob: Mob, source_group_id: str, target_group: AnimalGroupType,
        target_head_count: int, note: str | None = None, event_time: datetime | None = None,
    ) -> list[StockLedgerEntry]:
        """Reclassify this mob's entire type without rewriting cohort history."""
        from app.services.allocation_distribution_service import AllocationDistributionService
        from app.services.cohort_service import CohortService
        from app.services.grazing_history_service import GrazingHistoryService

        if isinstance(target_head_count, bool) or not isinstance(target_head_count, int):
            raise ValueError("Head count must be a whole number")
        if target_head_count < 0:
            raise ValueError("Head count must be greater than or equal to 0")
        if mob.status != "active":
            raise ValueError("Selected mob must be active")
        if target_group is None or db.session.get(AnimalGroupType, str(target_group.id)) is None:
            raise ValueError("Selected animal group type is invalid")
        if str(source_group_id) == str(target_group.id):
            return cls.adjust_group_stock(
                mob_id=mob.id, farm_id=mob.farm_id, animal_group_type_id=source_group_id,
                event_type=StockEventType.count, quantity=target_head_count,
                note=note, event_time=event_time,
            )
        balances = cls.group_balances(mob.id, source_group_id)
        if not balances:
            raise ValueError("Selected balance line is no longer available. Refresh and try again.")
        current = sum(int(row.head_count) for row in balances)
        removals = apportion_counts(
            max(0, current - target_head_count),
            [(str(row.id), int(row.head_count)) for row in balances],
        )
        posted_at = event_time or datetime.now(timezone.utc)
        if target_head_count < current and len(balances) > 1:
            note = cls.proportional_note(note)
        AllocationDistributionService.apply_reclassification(
            mob, source_group_id=str(source_group_id), target_group_id=str(target_group.id),
            target_head_count=target_head_count,
        )
        entries = []
        for balance in balances:
            parent = CohortService.ensure_balance_cohort(balance)
            original_count = int(balance.head_count)
            retained_count = original_count - removals[str(balance.id)]
            child = None
            if retained_count:
                state = {} if CohortService.is_female_group(target_group) else {
                    "reproductive_state": "not_recorded", "expected_litter_size": "not_recorded",
                    "lactation_state": "not_recorded", "offspring_at_foot": "not_recorded",
                }
                child = CohortService.child_cohort(
                    parent, quantity=retained_count, reason="correction",
                    animal_group_type_id=str(target_group.id), **state,
                )
            entries.append(cls.adjust_stock(
                mob_id=mob.id, farm_id=mob.farm_id, animal_group_type_id=source_group_id,
                cohort_id=parent.id, event_type=StockEventType.adjustment_out,
                quantity=original_count, note=note, event_time=posted_at,
                sync_grazing_history=False, sync_allocation_distribution=False,
            ))
            if child is not None:
                entries.append(cls.adjust_stock(
                    mob_id=mob.id, farm_id=mob.farm_id, animal_group_type_id=target_group.id,
                    cohort_id=child.id, event_type=StockEventType.adjustment_in,
                    quantity=retained_count, note=note, event_time=posted_at,
                    sync_grazing_history=False, sync_allocation_distribution=False,
                ))
        if target_head_count > current:
            entries.extend(cls.adjust_group_stock(
                mob_id=mob.id, farm_id=mob.farm_id, animal_group_type_id=target_group.id,
                event_type=StockEventType.adjustment_in, quantity=target_head_count - current,
                note=note, event_time=posted_at,
                sync_grazing_history=False, sync_allocation_distribution=False,
            ))
        GrazingHistoryService.sync_live_history_for_mob(mob, effective_at=posted_at)
        return entries

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
        event_type = StockEventType(event_type)
        if event_type == StockEventType.count:
            raise ValueError("Count events must be resolved to adjustment_in or missing before posting.")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError("quantity must be a whole number")
        ValidationService.validate_positive_int(quantity, "quantity")
        mob = db.session.get(Mob, str(mob_id))
        if mob is None or str(mob.farm_id) != str(farm_id):
            raise ValueError("Selected mob does not belong to this farm")
        if db.session.get(AnimalGroupType, str(animal_group_type_id)) is None:
            raise ValueError("Selected animal group type is invalid")
        posted_at = event_time or datetime.now(timezone.utc)

        from app.services.cohort_service import CohortService

        balance = CohortService.balance_for_selector(
            mob_id=str(mob_id),
            animal_group_type_id=str(animal_group_type_id),
            cohort_id=str(cohort_id) if cohort_id else None,
        )
        if event_type not in IN_EVENTS and (balance is None or quantity > int(balance.head_count)):
            raise ValueError("Stock balance cannot go negative")
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
                effective_at=posted_at,
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
