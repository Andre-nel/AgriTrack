from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class FemaleStatusObservation(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """A dated observation that updates the cached state of a counted female cohort."""

    __tablename__ = "female_status_observations"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    source_cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=False, index=True
    )
    result_cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=False, index=True
    )
    observed_on = db.Column(db.Date(), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    reproductive_state = db.Column(db.String(30), nullable=False)
    expected_litter_size = db.Column(db.String(20), nullable=False)
    lactation_state = db.Column(db.String(20), nullable=False)
    offspring_at_foot = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text, nullable=True)

    farm = db.relationship("Farm")
    mob = db.relationship("Mob")
    source_cohort = db.relationship("AnimalCohort", foreign_keys=[source_cohort_id])
    result_cohort = db.relationship("AnimalCohort", foreign_keys=[result_cohort_id])

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_female_status_observation_quantity_positive"),
        db.CheckConstraint(
            "reproductive_state IN ('not_recorded', 'with_sire', 'awaiting_scan', "
            "'pregnant', 'not_pregnant', 'parturated')",
            name="ck_female_status_observation_reproductive_state_allowed",
        ),
        db.CheckConstraint(
            "expected_litter_size IN ('not_recorded', 'unknown', 'single', 'twins', 'multiple')",
            name="ck_female_status_observation_litter_size_allowed",
        ),
        db.CheckConstraint(
            "lactation_state IN ('not_recorded', 'lactating', 'dry')",
            name="ck_female_status_observation_lactation_state_allowed",
        ),
        db.CheckConstraint(
            "offspring_at_foot IN ('not_recorded', 'none', 'single', 'twins', 'multiple')",
            name="ck_female_status_observation_offspring_at_foot_allowed",
        ),
    )


class BreedingCycle(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "breeding_cycles"

    name = db.Column(db.String(120), nullable=False)
    species = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    exposure_start_date = db.Column(db.Date(), nullable=False, index=True)
    exposure_end_date = db.Column(db.Date(), nullable=True, index=True)
    expected_parturition_start_date = db.Column(db.Date(), nullable=True)
    expected_parturition_end_date = db.Column(db.Date(), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    farm_links = db.relationship(
        "BreedingCycleFarm",
        back_populates="cycle",
        cascade="all, delete-orphan",
        order_by="BreedingCycleFarm.sort_order",
    )
    enrollments = db.relationship(
        "BreedingEnrollment", back_populates="cycle", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')",
            name="ck_breeding_cycle_species_allowed",
        ),
        db.CheckConstraint(
            "status IN ('open', 'closed')", name="ck_breeding_cycle_status_allowed"
        ),
        db.CheckConstraint(
            "exposure_end_date IS NULL OR exposure_end_date >= exposure_start_date",
            name="ck_breeding_cycle_exposure_date_order",
        ),
        db.CheckConstraint(
            "expected_parturition_end_date IS NULL OR expected_parturition_start_date IS NULL "
            "OR expected_parturition_end_date >= expected_parturition_start_date",
            name="ck_breeding_cycle_parturition_date_order",
        ),
    )

    @property
    def farms(self):
        return [link.farm for link in self.farm_links]

    @property
    def farm_ids(self) -> list[str]:
        return [str(link.farm_id) for link in self.farm_links]

    @property
    def farm_names(self) -> str:
        return ", ".join(link.farm.name for link in self.farm_links)


class BreedingCycleFarm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "breeding_cycle_farms"

    cycle_id = db.Column(
        db.String(36), db.ForeignKey("breeding_cycles.id"), nullable=False, index=True
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    cycle = db.relationship("BreedingCycle", back_populates="farm_links")
    farm = db.relationship("Farm", back_populates="breeding_cycle_links")

    __table_args__ = (
        db.CheckConstraint("sort_order >= 0", name="ck_breeding_cycle_farm_sort_non_negative"),
        db.UniqueConstraint("cycle_id", "farm_id", name="uq_breeding_cycle_farm"),
        db.UniqueConstraint("cycle_id", "sort_order", name="uq_breeding_cycle_farm_sort"),
    )


class BreedingEnrollment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "breeding_enrollments"

    cycle_id = db.Column(
        db.String(36), db.ForeignKey("breeding_cycles.id"), nullable=False, index=True
    )
    cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=False, index=True
    )
    source_mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    sire_cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=True, index=True
    )
    exposed_count = db.Column(db.Integer, nullable=False)
    exposure_start_date = db.Column(db.Date(), nullable=False, index=True)
    exposure_end_date = db.Column(db.Date(), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    notes = db.Column(db.Text, nullable=True)

    cycle = db.relationship("BreedingCycle", back_populates="enrollments")
    cohort = db.relationship("AnimalCohort", foreign_keys=[cohort_id])
    sire_cohort = db.relationship("AnimalCohort", foreign_keys=[sire_cohort_id])
    source_mob = db.relationship("Mob")
    pregnancy_assessments = db.relationship(
        "PregnancyAssessment", back_populates="enrollment", cascade="all, delete-orphan"
    )
    parturition_outcomes = db.relationship(
        "ParturitionOutcome", back_populates="enrollment", cascade="all, delete-orphan"
    )
    offspring_assessments = db.relationship(
        "OffspringAssessment", back_populates="enrollment", cascade="all, delete-orphan"
    )
    exceptions = db.relationship(
        "ReproductiveException", back_populates="enrollment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint("exposed_count > 0", name="ck_breeding_enrollment_exposed_positive"),
        db.CheckConstraint(
            "exposure_end_date IS NULL OR exposure_end_date >= exposure_start_date",
            name="ck_breeding_enrollment_exposure_date_order",
        ),
        db.CheckConstraint(
            "status IN ('active', 'completed', 'voided')",
            name="ck_breeding_enrollment_status_allowed",
        ),
        db.UniqueConstraint("cycle_id", "cohort_id", name="uq_breeding_enrollment_cycle_cohort"),
    )


class PregnancyAssessment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "pregnancy_assessments"

    enrollment_id = db.Column(
        db.String(36), db.ForeignKey("breeding_enrollments.id"), nullable=False, index=True
    )
    assessed_on = db.Column(db.Date(), nullable=False, index=True)
    pregnant_count = db.Column(db.Integer, nullable=False)
    not_pregnant_count = db.Column(db.Integer, nullable=False)
    unassessed_count = db.Column(db.Integer, nullable=False, default=0)
    expected_single_count = db.Column(db.Integer, nullable=False, default=0)
    expected_twin_count = db.Column(db.Integer, nullable=False, default=0)
    expected_multiple_count = db.Column(db.Integer, nullable=False, default=0)
    expected_unknown_litter_count = db.Column(db.Integer, nullable=False, default=0)
    expected_offspring_total = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    supersedes_id = db.Column(
        db.String(36), db.ForeignKey("pregnancy_assessments.id"), nullable=True, index=True
    )
    voided_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    enrollment = db.relationship("BreedingEnrollment", back_populates="pregnancy_assessments")
    supersedes = db.relationship("PregnancyAssessment", remote_side="PregnancyAssessment.id")

    __table_args__ = (
        db.CheckConstraint(
            "pregnant_count >= 0 AND not_pregnant_count >= 0 AND unassessed_count >= 0",
            name="ck_pregnancy_assessment_counts_non_negative",
        ),
        db.CheckConstraint(
            "expected_single_count >= 0 AND expected_twin_count >= 0 "
            "AND expected_multiple_count >= 0 AND expected_unknown_litter_count >= 0",
            name="ck_pregnancy_assessment_litters_non_negative",
        ),
        db.CheckConstraint(
            "expected_offspring_total IS NULL OR expected_offspring_total >= 0",
            name="ck_pregnancy_assessment_expected_total_non_negative",
        ),
    )


class ParturitionOutcome(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "parturition_outcomes"

    enrollment_id = db.Column(
        db.String(36), db.ForeignKey("breeding_enrollments.id"), nullable=False, index=True
    )
    period_start_date = db.Column(db.Date(), nullable=False, index=True)
    period_end_date = db.Column(db.Date(), nullable=True, index=True)
    females_parturated_count = db.Column(db.Integer, nullable=False)
    single_parturition_count = db.Column(db.Integer, nullable=False, default=0)
    twin_parturition_count = db.Column(db.Integer, nullable=False, default=0)
    multiple_parturition_count = db.Column(db.Integer, nullable=False, default=0)
    unknown_litter_parturition_count = db.Column(db.Integer, nullable=False, default=0)
    offspring_born_total = db.Column(db.Integer, nullable=False)
    offspring_born_alive = db.Column(db.Integer, nullable=False)
    offspring_stillborn = db.Column(db.Integer, nullable=False, default=0)
    strong_at_birth_count = db.Column(db.Integer, nullable=False, default=0)
    unassessed_at_birth_count = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)
    supersedes_id = db.Column(
        db.String(36), db.ForeignKey("parturition_outcomes.id"), nullable=True, index=True
    )
    voided_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    enrollment = db.relationship("BreedingEnrollment", back_populates="parturition_outcomes")
    supersedes = db.relationship("ParturitionOutcome", remote_side="ParturitionOutcome.id")
    stock_entries = db.relationship(
        "ParturitionStockEntry", back_populates="outcome", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint(
            "period_end_date IS NULL OR period_end_date >= period_start_date",
            name="ck_parturition_outcome_date_order",
        ),
        db.CheckConstraint(
            "females_parturated_count >= 0 AND single_parturition_count >= 0 "
            "AND twin_parturition_count >= 0 AND multiple_parturition_count >= 0 "
            "AND unknown_litter_parturition_count >= 0",
            name="ck_parturition_outcome_female_counts_non_negative",
        ),
        db.CheckConstraint(
            "offspring_born_total >= 0 AND offspring_born_alive >= 0 "
            "AND offspring_stillborn >= 0 AND strong_at_birth_count >= 0 "
            "AND unassessed_at_birth_count >= 0",
            name="ck_parturition_outcome_offspring_counts_non_negative",
        ),
    )


class ParturitionStockEntry(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "parturition_stock_entries"

    outcome_id = db.Column(
        db.String(36), db.ForeignKey("parturition_outcomes.id"), nullable=False, index=True
    )
    stock_ledger_entry_id = db.Column(
        db.String(36), db.ForeignKey("stock_ledger_entries.id"), nullable=False, unique=True, index=True
    )

    outcome = db.relationship("ParturitionOutcome", back_populates="stock_entries")
    stock_ledger_entry = db.relationship("StockLedgerEntry")

    __table_args__ = (
        db.UniqueConstraint("outcome_id", "stock_ledger_entry_id", name="uq_parturition_stock_entry"),
    )


class OffspringAssessment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "offspring_assessments"

    enrollment_id = db.Column(
        db.String(36), db.ForeignKey("breeding_enrollments.id"), nullable=False, index=True
    )
    assessed_on = db.Column(db.Date(), nullable=False, index=True)
    stage = db.Column(db.String(30), nullable=False, index=True)
    present_count = db.Column(db.Integer, nullable=False)
    assessed_count = db.Column(db.Integer, nullable=False)
    strong_count = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    supersedes_id = db.Column(
        db.String(36), db.ForeignKey("offspring_assessments.id"), nullable=True, index=True
    )
    voided_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    enrollment = db.relationship("BreedingEnrollment", back_populates="offspring_assessments")
    supersedes = db.relationship("OffspringAssessment", remote_side="OffspringAssessment.id")

    __table_args__ = (
        db.CheckConstraint(
            "stage IN ('marking', 'weaning', 'other')",
            name="ck_offspring_assessment_stage_allowed",
        ),
        db.CheckConstraint(
            "present_count >= 0 AND assessed_count >= 0 AND strong_count >= 0",
            name="ck_offspring_assessment_counts_non_negative",
        ),
    )


class ReproductiveException(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "reproductive_exceptions"

    enrollment_id = db.Column(
        db.String(36), db.ForeignKey("breeding_enrollments.id"), nullable=False, index=True
    )
    observed_on = db.Column(db.Date(), nullable=False, index=True)
    event_type = db.Column(db.String(30), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    supersedes_id = db.Column(
        db.String(36), db.ForeignKey("reproductive_exceptions.id"), nullable=True, index=True
    )
    voided_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    enrollment = db.relationship("BreedingEnrollment", back_populates="exceptions")
    supersedes = db.relationship("ReproductiveException", remote_side="ReproductiveException.id")

    __table_args__ = (
        db.CheckConstraint(
            "event_type IN ('pregnancy_loss', 'offspring_reassignment', 'other')",
            name="ck_reproductive_exception_type_allowed",
        ),
        db.CheckConstraint("quantity > 0", name="ck_reproductive_exception_quantity_positive"),
    )
