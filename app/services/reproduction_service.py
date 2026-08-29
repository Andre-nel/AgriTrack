from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Mapping

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    BreedingCycle,
    BreedingCycleFarm,
    BreedingEnrollment,
    Farm,
    FemaleStatusObservation,
    Mob,
    OffspringAssessment,
    ParturitionOutcome,
    ParturitionStockEntry,
    PregnancyAssessment,
    ReproductiveException,
)
from app.models.cohort import (
    LACTATION_STATES,
    LITTER_SIZE_STATES,
    OFFSPRING_AT_FOOT_STATES,
    REPRODUCTIVE_STATES,
)
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.stock_service import StockService


class ReproductionService:
    ASSESSMENT_STAGES = ("marking", "weaning", "other")
    EXCEPTION_TYPES = ("pregnancy_loss", "offspring_reassignment", "other")

    @staticmethod
    def _text(value, *, required: bool = False, label: str = "Value") -> str:
        normalized = " ".join(str(value or "").strip().split())
        if required and not normalized:
            raise ValueError(f"{label} is required")
        return normalized

    @staticmethod
    def _date(value, label: str, *, required: bool = True) -> date | None:
        raw = str(value or "").strip()
        if not raw:
            if required:
                raise ValueError(f"{label} is required")
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a valid date (YYYY-MM-DD)") from exc

    @staticmethod
    def _count(value, label: str, *, positive: bool = False, optional: bool = False) -> int | None:
        raw = str(value if value is not None else "").strip()
        if not raw and optional:
            return None
        try:
            number = int(raw or "0")
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number") from exc
        if positive and number <= 0:
            raise ValueError(f"{label} must be greater than 0")
        if not positive and number < 0:
            raise ValueError(f"{label} cannot be negative")
        return number

    @classmethod
    def _values(cls, form, key: str) -> list[str]:
        if hasattr(form, "getlist"):
            raw_values = form.getlist(key)
        else:
            raw = form.get(key)
            raw_values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        values = []
        for raw_value in raw_values:
            value = cls._text(raw_value)
            if value and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _effective(rows: list) -> list:
        superseded_ids = {
            str(row.supersedes_id)
            for row in rows
            if row.supersedes_id and row.voided_at is None
        }
        return [
            row
            for row in rows
            if row.voided_at is None and str(row.id) not in superseded_ids
        ]

    @classmethod
    def latest_effective(cls, rows: list, date_field: str):
        effective = cls._effective(rows)
        if not effective:
            return None
        return max(
            effective,
            key=lambda row: (
                getattr(row, date_field),
                row.created_at or datetime.min,
                str(row.id),
            ),
        )

    @classmethod
    def create_cycle_from_form(cls, form: Mapping[str, str]) -> BreedingCycle:
        farm_ids = cls._values(form, "farm_ids")
        if not farm_ids:
            legacy_farm_id = cls._text(form.get("farm_id"))
            farm_ids = [legacy_farm_id] if legacy_farm_id else []
        if not farm_ids:
            raise ValueError("At least one farm is required")
        farms_by_id = {
            str(farm.id): farm
            for farm in Farm.query.filter(Farm.id.in_(farm_ids), Farm.active.is_(True)).all()
        }
        if set(farms_by_id) != set(farm_ids):
            raise ValueError("One or more selected farms are invalid")
        name = cls._text(form.get("name"), required=True, label="Cycle name")
        species = StockService.normalize_species(form.get("species") or "")
        start_date = cls._date(form.get("exposure_start_date"), "Exposure start date")
        end_date = cls._date(
            form.get("exposure_end_date"), "Exposure end date", required=False
        )
        expected_start = cls._date(
            form.get("expected_parturition_start_date"),
            "Expected parturition start date",
            required=False,
        )
        expected_end = cls._date(
            form.get("expected_parturition_end_date"),
            "Expected parturition end date",
            required=False,
        )
        if end_date and end_date < start_date:
            raise ValueError("Exposure end date must be on or after the start date")
        if expected_start and expected_end and expected_end < expected_start:
            raise ValueError("Expected parturition end date must be on or after its start date")
        selected_farms = set(farm_ids)
        for existing in BreedingCycle.query.filter_by(name=name).all():
            if set(existing.farm_ids) == selected_farms:
                raise ValueError(
                    "A breeding cycle with this name already exists for the selected farms"
                )
        cycle = BreedingCycle(
            name=name,
            species=species,
            exposure_start_date=start_date,
            exposure_end_date=end_date,
            expected_parturition_start_date=expected_start,
            expected_parturition_end_date=expected_end,
            notes=cls._text(form.get("notes")) or None,
        )
        db.session.add(cycle)
        for sort_order, farm_id in enumerate(farm_ids):
            cycle.farm_links.append(
                BreedingCycleFarm(
                    farm=farms_by_id[farm_id],
                    sort_order=sort_order,
                )
            )
        db.session.commit()
        return cycle

    @classmethod
    def record_female_status_from_form(
        cls, mob: Mob, form: Mapping[str, str]
    ) -> FemaleStatusObservation:
        balance_id = cls._text(form.get("balance_id"), required=True, label="Female cohort")
        balance = db.session.get(AnimalGroupBalance, balance_id)
        if balance is None or str(balance.mob_id) != str(mob.id) or int(balance.head_count) <= 0:
            raise ValueError("Selected female cohort is no longer available in this mob")
        if not CohortService.is_female_group(balance.animal_group_type):
            raise ValueError("Current female state can only be recorded for female cohorts")
        quantity = cls._count(form.get("quantity"), "Observed females", positive=True)
        if quantity > int(balance.head_count):
            raise ValueError("Observed females cannot exceed the selected cohort balance")

        values = {
            "reproductive_state": cls._text(
                form.get("reproductive_state"), required=True, label="Reproductive state"
            ),
            "expected_litter_size": cls._text(
                form.get("expected_litter_size"), required=True, label="Expected litter size"
            ),
            "lactation_state": cls._text(
                form.get("lactation_state"), required=True, label="Lactation state"
            ),
            "offspring_at_foot": cls._text(
                form.get("offspring_at_foot"), required=True, label="Offspring at foot"
            ),
        }
        allowed = {
            "reproductive_state": REPRODUCTIVE_STATES,
            "expected_litter_size": LITTER_SIZE_STATES,
            "lactation_state": LACTATION_STATES,
            "offspring_at_foot": OFFSPRING_AT_FOOT_STATES,
        }
        for field, field_values in allowed.items():
            if values[field] not in field_values:
                raise ValueError(f"Selected {field.replace('_', ' ')} is invalid")

        source_cohort = CohortService.ensure_balance_cohort(balance)
        result_balance = CohortService.partition_balance(
            balance,
            [{"quantity": quantity, **values}],
            reason="reproductive_partition",
        )[0]
        result_cohort = CohortService.ensure_balance_cohort(result_balance)
        observation = FemaleStatusObservation(
            farm_id=mob.farm_id,
            mob_id=mob.id,
            source_cohort_id=source_cohort.id,
            result_cohort_id=result_cohort.id,
            observed_on=cls._date(form.get("observed_on"), "Observation date"),
            quantity=quantity,
            notes=cls._text(form.get("notes")) or None,
            **values,
        )
        db.session.add(observation)

        from app.services.mob_event_service import MobEventService

        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=(
                f"Female cohort state observed for {quantity} head: "
                f"{values['reproductive_state'].replace('_', ' ')}, "
                f"{values['lactation_state'].replace('_', ' ')}, offspring at foot "
                f"{values['offspring_at_foot'].replace('_', ' ')}."
            ),
            raw_tags="reproduction,observation,female state",
        )
        db.session.commit()
        return observation

    @classmethod
    def record_female_status_from_form(
        cls, mob: Mob, form: Mapping[str, str]
    ) -> FemaleStatusObservation:
        balance_id = cls._text(form.get("balance_id"), required=True, label="Female cohort")
        balance = db.session.get(AnimalGroupBalance, balance_id)
        if balance is None or str(balance.mob_id) != str(mob.id) or int(balance.head_count) <= 0:
            raise ValueError("Selected female cohort is no longer available in this mob")
        if not CohortService.is_female_group(balance.animal_group_type):
            raise ValueError("Current female state can only be recorded for female cohorts")
        quantity = cls._count(form.get("quantity"), "Observed females", positive=True)
        if quantity > int(balance.head_count):
            raise ValueError("Observed females cannot exceed the selected cohort balance")

        values = {
            "reproductive_state": cls._text(
                form.get("reproductive_state"), required=True, label="Reproductive state"
            ),
            "expected_litter_size": cls._text(
                form.get("expected_litter_size"), required=True, label="Expected litter size"
            ),
            "lactation_state": cls._text(
                form.get("lactation_state"), required=True, label="Lactation state"
            ),
            "offspring_at_foot": cls._text(
                form.get("offspring_at_foot"), required=True, label="Offspring at foot"
            ),
        }
        allowed = {
            "reproductive_state": REPRODUCTIVE_STATES,
            "expected_litter_size": LITTER_SIZE_STATES,
            "lactation_state": LACTATION_STATES,
            "offspring_at_foot": OFFSPRING_AT_FOOT_STATES,
        }
        for field, field_values in allowed.items():
            if values[field] not in field_values:
                raise ValueError(f"Selected {field.replace('_', ' ')} is invalid")

        source_cohort = CohortService.ensure_balance_cohort(balance)
        result_balance = CohortService.partition_balance(
            balance,
            [{"quantity": quantity, **values}],
            reason="reproductive_partition",
        )[0]
        result_cohort = CohortService.ensure_balance_cohort(result_balance)
        observation = FemaleStatusObservation(
            farm_id=mob.farm_id,
            mob_id=mob.id,
            source_cohort_id=source_cohort.id,
            result_cohort_id=result_cohort.id,
            observed_on=cls._date(form.get("observed_on"), "Observation date"),
            quantity=quantity,
            notes=cls._text(form.get("notes")) or None,
            **values,
        )
        db.session.add(observation)

        from app.services.mob_event_service import MobEventService

        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=(
                f"Female cohort state observed for {quantity} head: "
                f"{values['reproductive_state'].replace('_', ' ')}, "
                f"{values['lactation_state'].replace('_', ' ')}, offspring at foot "
                f"{values['offspring_at_foot'].replace('_', ' ')}."
            ),
            raw_tags="reproduction,observation,female state",
        )
        db.session.commit()
        return observation

    @classmethod
    def enroll_from_form(
        cls, cycle: BreedingCycle, form: Mapping[str, str]
    ) -> BreedingEnrollment:
        if cycle.status != "open":
            raise ValueError("Closed breeding cycles cannot accept new female cohorts")
        balance_id = cls._text(form.get("balance_id"), required=True, label="Female cohort")
        balance = db.session.get(AnimalGroupBalance, balance_id)
        if balance is None or int(balance.head_count or 0) <= 0:
            raise ValueError("Selected female cohort is no longer available")
        mob = db.session.get(Mob, balance.mob_id)
        if mob is None or str(mob.farm_id) not in set(cycle.farm_ids):
            raise ValueError("Selected female cohort does not belong to a breeding-cycle farm")
        group = balance.animal_group_type
        if group.species != cycle.species:
            raise ValueError("Selected female cohort does not match the breeding-cycle species")
        if not CohortService.is_female_group(group):
            raise ValueError("Only female animal cohorts can be enrolled")

        exposed_count = cls._count(
            form.get("exposed_count"), "Females exposed", positive=True
        )
        if exposed_count > int(balance.head_count):
            raise ValueError("Females exposed cannot exceed the selected cohort balance")

        source_cohort = CohortService.ensure_balance_cohort(balance)
        for existing_enrollment in cycle.enrollments:
            if str(source_cohort.id) in CohortService.descendants(
                str(existing_enrollment.cohort_id)
            ):
                raise ValueError("This female cohort is already represented in the breeding cycle")

        start_date = cls._date(
            form.get("exposure_start_date") or cycle.exposure_start_date.isoformat(),
            "Exposure start date",
        )
        end_date = cls._date(
            form.get("exposure_end_date")
            or (cycle.exposure_end_date.isoformat() if cycle.exposure_end_date else ""),
            "Exposure end date",
            required=False,
        )
        if end_date and end_date < start_date:
            raise ValueError("Exposure end date must be on or after the start date")

        sire_cohort_id = cls._text(form.get("sire_cohort_id")) or None
        if sire_cohort_id:
            sire = db.session.get(AnimalCohort, sire_cohort_id)
            if sire is None or sire.animal_group_type.species != cycle.species:
                raise ValueError("Selected sire cohort is invalid for this cycle")
            if not CohortService.is_sire_group(sire.animal_group_type):
                raise ValueError("Selected sire cohort is not a breeding-male animal group")
            sire_available = (
                AnimalGroupBalance.query.join(Mob, AnimalGroupBalance.mob_id == Mob.id)
                .filter(
                    AnimalGroupBalance.cohort_id == sire_cohort_id,
                    AnimalGroupBalance.head_count > 0,
                    Mob.farm_id.in_(cycle.farm_ids),
                )
                .first()
            )
            if sire_available is None:
                raise ValueError("Selected sire cohort is not present on a breeding-cycle farm")

        selected_balance = CohortService.partition_balance(
            balance,
            [
                {
                    "quantity": exposed_count,
                    "reproductive_state": "with_sire",
                }
            ],
        )[0]
        cohort = CohortService.ensure_balance_cohort(selected_balance)
        existing = BreedingEnrollment.query.filter_by(
            cycle_id=cycle.id, cohort_id=cohort.id
        ).first()
        if existing:
            raise ValueError("This cohort is already enrolled in the breeding cycle")

        enrollment = BreedingEnrollment(
            cycle_id=cycle.id,
            cohort_id=cohort.id,
            source_mob_id=mob.id,
            sire_cohort_id=sire_cohort_id,
            exposed_count=exposed_count,
            exposure_start_date=start_date,
            exposure_end_date=end_date,
            notes=cls._text(form.get("notes")) or None,
        )
        db.session.add(enrollment)
        db.session.commit()
        return enrollment

    @classmethod
    def _descendant_balances(
        cls,
        enrollment: BreedingEnrollment,
        *,
        states: set[str],
    ) -> list[AnimalGroupBalance]:
        cohort_ids = CohortService.descendants(str(enrollment.cohort_id))
        rows = (
            AnimalGroupBalance.query.join(AnimalCohort, AnimalGroupBalance.cohort_id == AnimalCohort.id)
            .filter(
                AnimalGroupBalance.cohort_id.in_(cohort_ids),
                AnimalGroupBalance.head_count > 0,
                AnimalCohort.reproductive_state.in_(states),
            )
            .order_by(AnimalGroupBalance.created_at.asc(), AnimalGroupBalance.id.asc())
            .all()
        )
        return rows

    @classmethod
    def _partition_descendants(
        cls,
        enrollment: BreedingEnrollment,
        partitions: list[dict],
        *,
        allowed_states: set[str],
    ) -> None:
        remaining = [dict(row, quantity=int(row["quantity"])) for row in partitions if int(row["quantity"]) > 0]
        required = sum(row["quantity"] for row in remaining)
        candidates = cls._descendant_balances(enrollment, states=allowed_states)
        available = sum(int(row.head_count or 0) for row in candidates)
        if required > available:
            raise ValueError(
                "Recorded outcome count exceeds the currently available cohort descendants "
                f"({required} requested, {available} available)"
            )

        for balance in candidates:
            if not remaining:
                break
            capacity = int(balance.head_count or 0)
            local = []
            while capacity > 0 and remaining:
                current = remaining[0]
                allocated = min(capacity, current["quantity"])
                local.append({**current, "quantity": allocated})
                capacity -= allocated
                current["quantity"] -= allocated
                if current["quantity"] == 0:
                    remaining.pop(0)
            if local:
                CohortService.partition_balance(balance, local)
        if remaining:
            raise ValueError("Unable to partition all recorded reproductive outcomes")

    @classmethod
    def record_pregnancy_assessment_from_form(
        cls, enrollment: BreedingEnrollment, form: Mapping[str, str]
    ) -> PregnancyAssessment:
        assessed_on = cls._date(form.get("assessed_on"), "Assessment date")
        pregnant = cls._count(form.get("pregnant_count"), "Pregnant females")
        not_pregnant = cls._count(form.get("not_pregnant_count"), "Not-pregnant females")
        unassessed = cls._count(form.get("unassessed_count"), "Unassessed females")
        if pregnant + not_pregnant + unassessed != int(enrollment.exposed_count):
            raise ValueError(
                "Pregnant, not-pregnant, and unassessed counts must equal females exposed"
            )
        single = cls._count(form.get("expected_single_count"), "Expected singleton females")
        twins = cls._count(form.get("expected_twin_count"), "Expected twin females")
        multiple = cls._count(
            form.get("expected_multiple_count"), "Expected multiple-bearing females"
        )
        unknown = cls._count(
            form.get("expected_unknown_litter_count"), "Unknown-litter pregnant females"
        )
        if single + twins + multiple + unknown != pregnant:
            raise ValueError(
                "Expected singleton, twin, multiple, and unknown-litter counts must equal pregnant females"
            )
        expected_total = cls._count(
            form.get("expected_offspring_total"),
            "Expected offspring total",
            optional=True,
        )
        minimum_expected = single + (2 * twins) + (3 * multiple) + unknown
        if expected_total is not None and expected_total < minimum_expected:
            raise ValueError(
                "Expected offspring total cannot be below the minimum represented by litter counts"
            )
        if expected_total is None and unknown == 0:
            expected_total = minimum_expected

        existing = cls.latest_effective(enrollment.pregnancy_assessments, "assessed_on")
        supersedes_id = cls._text(form.get("supersedes_id")) or None
        if existing and supersedes_id != str(existing.id):
            raise ValueError("This enrollment already has a pregnancy assessment; correct the current observation")
        if not existing and supersedes_id:
            raise ValueError("The pregnancy observation selected for correction is no longer current")

        if existing is None:
            partitions = []
            for quantity, litter_size in (
                (single, "single"),
                (twins, "twins"),
                (multiple, "multiple"),
                (unknown, "unknown"),
            ):
                if quantity:
                    partitions.append(
                        {
                            "quantity": quantity,
                            "reproductive_state": "pregnant",
                            "expected_litter_size": litter_size,
                        }
                    )
            if not_pregnant:
                partitions.append(
                    {
                        "quantity": not_pregnant,
                        "reproductive_state": "not_pregnant",
                        "expected_litter_size": "not_recorded",
                    }
                )
            cls._partition_descendants(
                enrollment,
                partitions,
                allowed_states={"with_sire", "awaiting_scan"},
            )

        assessment = PregnancyAssessment(
            enrollment_id=enrollment.id,
            assessed_on=assessed_on,
            pregnant_count=pregnant,
            not_pregnant_count=not_pregnant,
            unassessed_count=unassessed,
            expected_single_count=single,
            expected_twin_count=twins,
            expected_multiple_count=multiple,
            expected_unknown_litter_count=unknown,
            expected_offspring_total=expected_total,
            notes=cls._text(form.get("notes")) or None,
            supersedes_id=supersedes_id,
        )
        db.session.add(assessment)
        db.session.commit()
        return assessment

    @classmethod
    def record_parturition_from_form(
        cls, enrollment: BreedingEnrollment, form: Mapping[str, str]
    ) -> ParturitionOutcome:
        period_start = cls._date(form.get("period_start_date"), "Parturition start date")
        period_end = cls._date(
            form.get("period_end_date"), "Parturition end date", required=False
        )
        if period_end and period_end < period_start:
            raise ValueError("Parturition end date must be on or after the start date")
        parturated = cls._count(form.get("females_parturated_count"), "Females parturated")
        single = cls._count(form.get("single_parturition_count"), "Single parturitions")
        twins = cls._count(form.get("twin_parturition_count"), "Twin parturitions")
        multiple = cls._count(form.get("multiple_parturition_count"), "Multiple parturitions")
        unknown = cls._count(
            form.get("unknown_litter_parturition_count"), "Unknown-litter parturitions"
        )
        if single + twins + multiple + unknown != parturated:
            raise ValueError("Parturition litter counts must equal females parturated")
        total_born = cls._count(form.get("offspring_born_total"), "Total offspring born")
        born_alive = cls._count(form.get("offspring_born_alive"), "Offspring born alive")
        stillborn = cls._count(form.get("offspring_stillborn"), "Stillborn offspring")
        strong = cls._count(form.get("strong_at_birth_count"), "Strong offspring at birth")
        unassessed = cls._count(
            form.get("unassessed_at_birth_count"), "Unassessed offspring at birth"
        )
        if born_alive + stillborn != total_born:
            raise ValueError("Born-alive and stillborn counts must equal total births")
        minimum_births = single + (2 * twins) + (3 * multiple) + unknown
        if total_born < minimum_births:
            raise ValueError("Total births cannot be below the minimum represented by litter counts")
        if strong + unassessed > born_alive:
            raise ValueError("Strong and unassessed counts cannot exceed offspring born alive")
        if parturated > int(enrollment.exposed_count):
            raise ValueError("Females parturated cannot exceed females exposed")
        existing = cls.latest_effective(enrollment.parturition_outcomes, "period_start_date")
        supersedes_id = cls._text(form.get("supersedes_id")) or None
        if existing and supersedes_id != str(existing.id):
            raise ValueError("This enrollment already has a parturition outcome; correct the current observation")
        if not existing and supersedes_id:
            raise ValueError("The parturition observation selected for correction is no longer current")
        if existing and born_alive != int(existing.offspring_born_alive):
            raise ValueError(
                "A correction cannot change live births after stock was posted; use a stock adjustment "
                "and record the reason before correcting the observation"
            )

        if existing is None:
            partitions = (
                [{"quantity": parturated, "reproductive_state": "parturated"}]
                if parturated
                else []
            )
            cls._partition_descendants(
                enrollment,
                partitions,
                allowed_states={"with_sire", "awaiting_scan", "pregnant"},
            )

        outcome = ParturitionOutcome(
            enrollment_id=enrollment.id,
            period_start_date=period_start,
            period_end_date=period_end,
            females_parturated_count=parturated,
            single_parturition_count=single,
            twin_parturition_count=twins,
            multiple_parturition_count=multiple,
            unknown_litter_parturition_count=unknown,
            offspring_born_total=total_born,
            offspring_born_alive=born_alive,
            offspring_stillborn=stillborn,
            strong_at_birth_count=strong,
            unassessed_at_birth_count=unassessed,
            notes=cls._text(form.get("notes")) or None,
            supersedes_id=supersedes_id,
        )
        db.session.add(outcome)
        db.session.flush()

        if born_alive > 0 and existing is None:
            offspring_mob_id = cls._text(
                form.get("offspring_mob_id"), required=True, label="Offspring mob"
            )
            offspring_mob = db.session.get(Mob, offspring_mob_id)
            if (
                offspring_mob is None
                or str(offspring_mob.farm_id) not in set(enrollment.cycle.farm_ids)
            ):
                raise ValueError("Selected offspring mob is invalid")
            offspring_species = enrollment.cycle.species
            offspring_age = StockService.AGE_CLASS_OPTIONS[offspring_species][0]
            offspring_group = StockService.get_or_create_group_type(
                species=offspring_species,
                breed=cls._text(form.get("offspring_breed"), required=True, label="Offspring breed"),
                sex=cls._text(form.get("offspring_sex")) or "mixed",
                age_class=offspring_age,
            )
            offspring_cohort = CohortService.create_cohort(
                animal_group_type_id=str(offspring_group.id),
                origin_farm_id=str(offspring_mob.farm_id),
                origin="birth",
            )
            ledger = StockService.adjust_stock(
                mob_id=offspring_mob.id,
                farm_id=offspring_mob.farm_id,
                animal_group_type_id=offspring_group.id,
                cohort_id=offspring_cohort.id,
                event_type=StockEventType.birth,
                quantity=born_alive,
                note=f"Live births from breeding cycle {enrollment.cycle.name}",
                event_time=datetime.combine(period_end or period_start, datetime.min.time()).replace(
                    tzinfo=timezone.utc
                ),
            )
            db.session.add(
                ParturitionStockEntry(
                    outcome_id=outcome.id,
                    stock_ledger_entry_id=ledger.id,
                )
            )
        enrollment.status = "completed"
        db.session.commit()
        return outcome

    @classmethod
    def record_offspring_assessment_from_form(
        cls, enrollment: BreedingEnrollment, form: Mapping[str, str]
    ) -> OffspringAssessment:
        stage = cls._text(form.get("stage"), required=True, label="Assessment stage").lower()
        if stage not in cls.ASSESSMENT_STAGES:
            raise ValueError("Assessment stage must be marking, weaning, or other")
        present = cls._count(form.get("present_count"), "Offspring present")
        assessed = cls._count(form.get("assessed_count"), "Offspring assessed")
        strong = cls._count(form.get("strong_count"), "Strong offspring")
        if assessed > present:
            raise ValueError("Offspring assessed cannot exceed offspring present")
        if strong > assessed:
            raise ValueError("Strong offspring cannot exceed offspring assessed")
        assessment = OffspringAssessment(
            enrollment_id=enrollment.id,
            assessed_on=cls._date(form.get("assessed_on"), "Assessment date"),
            stage=stage,
            present_count=present,
            assessed_count=assessed,
            strong_count=strong,
            notes=cls._text(form.get("notes")) or None,
            supersedes_id=cls._text(form.get("supersedes_id")) or None,
        )
        db.session.add(assessment)
        db.session.commit()
        return assessment

    @classmethod
    def record_exception_from_form(
        cls, enrollment: BreedingEnrollment, form: Mapping[str, str]
    ) -> ReproductiveException:
        event_type = cls._text(form.get("event_type"), required=True, label="Exception type").lower()
        if event_type not in cls.EXCEPTION_TYPES:
            raise ValueError("Exception type is invalid")
        row = ReproductiveException(
            enrollment_id=enrollment.id,
            observed_on=cls._date(form.get("observed_on"), "Observation date"),
            event_type=event_type,
            quantity=cls._count(form.get("quantity"), "Exception count", positive=True),
            notes=cls._text(form.get("notes")) or None,
            supersedes_id=cls._text(form.get("supersedes_id")) or None,
        )
        db.session.add(row)
        db.session.commit()
        return row

    @classmethod
    def void_observation(cls, row) -> None:
        if getattr(row, "voided_at", None) is not None:
            return
        row.voided_at = datetime.now(timezone.utc)
        db.session.commit()

    @classmethod
    def build_analytics_report(
        cls,
        cycles: list[BreedingCycle],
        *,
        farm_id: str | None = None,
    ) -> dict:
        exposed = 0
        parturated = 0
        pregnant = 0
        expected_offspring = 0
        expected_quantified_pregnant = 0
        total_births = 0
        strong_at_birth = 0
        birth_observed_exposed = 0
        unassessed_scan = 0
        unassessed_birth = 0
        enrollment_rows = []
        later_totals: dict[str, dict[str, int]] = {
            stage: {"present": 0, "assessed": 0, "strong": 0}
            for stage in cls.ASSESSMENT_STAGES
        }

        for cycle in cycles:
            for enrollment in cycle.enrollments:
                if enrollment.status == "voided":
                    continue
                if farm_id and str(enrollment.source_mob.farm_id) != str(farm_id):
                    continue
                exposed += int(enrollment.exposed_count)
                scan = cls.latest_effective(enrollment.pregnancy_assessments, "assessed_on")
                birth = cls.latest_effective(enrollment.parturition_outcomes, "period_start_date")
                if scan:
                    pregnant += int(scan.pregnant_count)
                    unassessed_scan += int(scan.unassessed_count)
                    if scan.expected_offspring_total is not None:
                        expected_offspring += int(scan.expected_offspring_total)
                        expected_quantified_pregnant += int(scan.pregnant_count)
                if birth:
                    birth_observed_exposed += int(enrollment.exposed_count)
                    parturated += int(birth.females_parturated_count)
                    total_births += int(birth.offspring_born_total)
                    strong_at_birth += int(birth.strong_at_birth_count)
                    unassessed_birth += int(birth.unassessed_at_birth_count)

                latest_by_stage = {}
                for assessment in cls._effective(enrollment.offspring_assessments):
                    current = latest_by_stage.get(assessment.stage)
                    if current is None or (assessment.assessed_on, str(assessment.id)) > (
                        current.assessed_on,
                        str(current.id),
                    ):
                        latest_by_stage[assessment.stage] = assessment
                for stage, assessment in latest_by_stage.items():
                    later_totals[stage]["present"] += int(assessment.present_count)
                    later_totals[stage]["assessed"] += int(assessment.assessed_count)
                    later_totals[stage]["strong"] += int(assessment.strong_count)

                enrollment_rows.append(
                    {
                        "cycle_name": cycle.name,
                        "farm_name": enrollment.source_mob.farm.name,
                        "mob_name": enrollment.source_mob.name,
                        "group_label": CohortService.display_label(enrollment.cohort),
                        "exposed": int(enrollment.exposed_count),
                        "pregnant": int(scan.pregnant_count) if scan else None,
                        "expected_offspring": (
                            int(scan.expected_offspring_total)
                            if scan and scan.expected_offspring_total is not None
                            else None
                        ),
                        "parturated": int(birth.females_parturated_count) if birth else None,
                        "total_births": int(birth.offspring_born_total) if birth else None,
                        "strong_at_birth": int(birth.strong_at_birth_count) if birth else None,
                    }
                )

        def rate(numerator: int, denominator: int):
            return (numerator / denominator * 100.0) if denominator > 0 else None

        def per(numerator: int, denominator: int):
            return (numerator / denominator) if denominator > 0 else None

        later_rows = []
        for stage, totals in later_totals.items():
            if not any(totals.values()):
                continue
            later_rows.append(
                {
                    "stage": stage,
                    **totals,
                    "strong_of_assessed_pct": rate(totals["strong"], totals["assessed"]),
                    "strong_of_births_pct": rate(totals["strong"], total_births),
                    "strong_per_exposed": per(totals["strong"], birth_observed_exposed),
                }
            )

        return {
            "totals": {
                "exposed": exposed,
                "pregnant": pregnant,
                "parturated": parturated,
                "expected_offspring": expected_offspring,
                "expected_quantified_pregnant": expected_quantified_pregnant,
                "total_births": total_births,
                "strong_at_birth": strong_at_birth,
                "birth_observed_exposed": birth_observed_exposed,
                "birth_unobserved_exposed": exposed - birth_observed_exposed,
                "expected_unquantified_pregnant": pregnant - expected_quantified_pregnant,
                "unassessed_scan": unassessed_scan,
                "unassessed_birth": unassessed_birth,
            },
            "metrics": {
                "parturition_rate_pct": rate(parturated, birth_observed_exposed),
                "expected_offspring_per_parturition": per(
                    expected_offspring, expected_quantified_pregnant
                ),
                "strong_at_birth_pct": rate(strong_at_birth, total_births),
                "strong_per_exposed_female": per(strong_at_birth, birth_observed_exposed),
            },
            "later_assessments": later_rows,
            "enrollments": enrollment_rows,
        }
