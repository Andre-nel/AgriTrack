from collections import defaultdict, deque

from app.extensions import db
from app.models import AnimalCohort, AnimalCohortLineage, AnimalGroupBalance, Mob


class CohortService:
    FEMALE_SEX_BY_SPECIES = {"Cattle": "cow", "Sheep": "ewe", "Goat": "ewe"}
    SIRE_SEX_BY_SPECIES = {"Cattle": "bul", "Sheep": "ram", "Goat": "ram"}

    @staticmethod
    def is_female_group(group_type) -> bool:
        return (
            CohortService.FEMALE_SEX_BY_SPECIES.get(group_type.species)
            == (group_type.sex or "").strip().lower()
        )

    @staticmethod
    def is_sire_group(group_type) -> bool:
        return (
            CohortService.SIRE_SEX_BY_SPECIES.get(group_type.species)
            == (group_type.sex or "").strip().lower()
        )

    @staticmethod
    def display_label(cohort: AnimalCohort | None, group_type=None) -> str:
        group = group_type or (cohort.animal_group_type if cohort else None)
        if group is None:
            return "Unknown animal cohort"
        label = f"{group.species} | {group.breed} | {group.sex} | {group.age_class}"
        if cohort is None:
            return label
        state = cohort.reproductive_state.replace("_", " ")
        lactation = cohort.lactation_state.replace("_", " ")
        offspring = cohort.offspring_at_foot.replace("_", " ")
        details = []
        if state != "not recorded":
            details.append(state)
        if lactation != "not recorded":
            details.append(lactation)
        if offspring != "not recorded":
            details.append(f"offspring at foot: {offspring}")
        if details:
            label = f"{label} | {' | '.join(details)}"
        return label

    @staticmethod
    def create_cohort(
        *,
        animal_group_type_id: str,
        origin_farm_id: str | None,
        origin: str = "manual",
        reproductive_state: str = "not_recorded",
        expected_litter_size: str = "not_recorded",
        lactation_state: str = "not_recorded",
        offspring_at_foot: str = "not_recorded",
        notes: str | None = None,
    ) -> AnimalCohort:
        cohort = AnimalCohort(
            animal_group_type_id=animal_group_type_id,
            origin_farm_id=origin_farm_id,
            origin=origin,
            reproductive_state=reproductive_state,
            expected_litter_size=expected_litter_size,
            lactation_state=lactation_state,
            offspring_at_foot=offspring_at_foot,
            notes=(notes or "").strip() or None,
        )
        db.session.add(cohort)
        db.session.flush()
        return cohort

    @classmethod
    def ensure_balance_cohort(
        cls,
        balance: AnimalGroupBalance,
        *,
        origin: str = "legacy",
    ) -> AnimalCohort:
        if balance.cohort is not None:
            return balance.cohort
        mob = balance.mob or db.session.get(Mob, balance.mob_id)
        cohort = cls.create_cohort(
            animal_group_type_id=str(balance.animal_group_type_id),
            origin_farm_id=str(mob.farm_id) if mob else None,
            origin=origin,
        )
        balance.cohort_id = cohort.id
        balance.cohort = cohort
        db.session.flush()
        return cohort

    @classmethod
    def balance_for_selector(
        cls,
        *,
        mob_id: str,
        animal_group_type_id: str | None = None,
        cohort_id: str | None = None,
        require_positive: bool = False,
    ) -> AnimalGroupBalance | None:
        if cohort_id:
            balance = AnimalGroupBalance.query.filter_by(mob_id=mob_id, cohort_id=cohort_id).first()
            if balance and animal_group_type_id and str(balance.animal_group_type_id) != str(
                animal_group_type_id
            ):
                raise ValueError("Selected cohort does not match the animal group type")
        else:
            rows = AnimalGroupBalance.query.filter_by(
                mob_id=mob_id,
                animal_group_type_id=animal_group_type_id,
            ).all()
            rows = [row for row in rows if not require_positive or int(row.head_count or 0) > 0]
            if len(rows) > 1:
                raise ValueError(
                    "This animal type contains multiple cohorts; select a specific cohort"
                )
            balance = rows[0] if rows else None
        if require_positive and balance is not None and int(balance.head_count or 0) <= 0:
            return None
        return balance

    @classmethod
    def child_cohort(
        cls,
        parent: AnimalCohort,
        *,
        quantity: int,
        reason: str,
        movement_event_id: str | None = None,
        reproductive_state: str | None = None,
        expected_litter_size: str | None = None,
        lactation_state: str | None = None,
        offspring_at_foot: str | None = None,
        origin_farm_id: str | None = None,
    ) -> AnimalCohort:
        child = cls.create_cohort(
            animal_group_type_id=str(parent.animal_group_type_id),
            origin_farm_id=origin_farm_id or parent.origin_farm_id,
            origin="split",
            reproductive_state=reproductive_state or parent.reproductive_state,
            expected_litter_size=expected_litter_size or parent.expected_litter_size,
            lactation_state=lactation_state or parent.lactation_state,
            offspring_at_foot=offspring_at_foot or parent.offspring_at_foot,
        )
        db.session.add(
            AnimalCohortLineage(
                parent_cohort_id=parent.id,
                child_cohort_id=child.id,
                movement_event_id=movement_event_id,
                quantity=int(quantity),
                reason=reason,
            )
        )
        db.session.flush()
        return child

    @classmethod
    def cohort_for_partial_transfer(
        cls,
        balance: AnimalGroupBalance,
        *,
        quantity: int,
        movement_event_id: str | None = None,
        destination_farm_id: str | None = None,
    ) -> AnimalCohort:
        parent = cls.ensure_balance_cohort(balance)
        if int(quantity) == int(balance.head_count):
            return parent
        if int(quantity) <= 0 or int(quantity) > int(balance.head_count):
            raise ValueError("Cohort transfer quantity is outside the available balance")
        return cls.child_cohort(
            parent,
            quantity=quantity,
            reason="stock_split",
            movement_event_id=movement_event_id,
            origin_farm_id=destination_farm_id,
        )

    @classmethod
    def partition_balance(
        cls,
        balance: AnimalGroupBalance,
        partitions: list[dict],
        *,
        reason: str = "reproductive_partition",
    ) -> list[AnimalGroupBalance]:
        clean = [dict(row, quantity=int(row.get("quantity", 0))) for row in partitions]
        clean = [row for row in clean if row["quantity"] > 0]
        total = sum(row["quantity"] for row in clean)
        available = int(balance.head_count or 0)
        if not clean:
            raise ValueError("At least one positive cohort partition is required")
        if total > available:
            raise ValueError("Cohort partitions cannot exceed the available female count")

        parent = cls.ensure_balance_cohort(balance)
        if len(clean) == 1 and total == available:
            row = clean[0]
            parent.reproductive_state = row.get("reproductive_state") or parent.reproductive_state
            parent.expected_litter_size = row.get("expected_litter_size") or parent.expected_litter_size
            parent.lactation_state = row.get("lactation_state") or parent.lactation_state
            parent.offspring_at_foot = row.get("offspring_at_foot") or parent.offspring_at_foot
            return [balance]

        result = []
        balance.head_count = available - total
        for row in clean:
            child = cls.child_cohort(
                parent,
                quantity=row["quantity"],
                reason=reason,
                reproductive_state=row.get("reproductive_state"),
                expected_litter_size=row.get("expected_litter_size"),
                lactation_state=row.get("lactation_state"),
                offspring_at_foot=row.get("offspring_at_foot"),
            )
            child_balance = AnimalGroupBalance(
                mob_id=balance.mob_id,
                animal_group_type_id=balance.animal_group_type_id,
                cohort_id=child.id,
                head_count=row["quantity"],
            )
            db.session.add(child_balance)
            result.append(child_balance)
        if balance.head_count == 0:
            db.session.delete(balance)
            parent.status = "closed"
        db.session.flush()
        return result

    @staticmethod
    def descendants(root_cohort_id: str) -> set[str]:
        rows = db.session.query(
            AnimalCohortLineage.parent_cohort_id,
            AnimalCohortLineage.child_cohort_id,
        ).all()
        children: dict[str, list[str]] = defaultdict(list)
        for parent_id, child_id in rows:
            children[str(parent_id)].append(str(child_id))
        discovered = {str(root_cohort_id)}
        pending = deque([str(root_cohort_id)])
        while pending:
            parent_id = pending.popleft()
            for child_id in children.get(parent_id, []):
                if child_id not in discovered:
                    discovered.add(child_id)
                    pending.append(child_id)
        return discovered
