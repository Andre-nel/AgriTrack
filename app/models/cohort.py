from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


REPRODUCTIVE_STATES = (
    "not_recorded",
    "with_sire",
    "awaiting_scan",
    "pregnant",
    "not_pregnant",
    "parturated",
)
LITTER_SIZE_STATES = ("not_recorded", "unknown", "single", "twins", "multiple")
LACTATION_STATES = ("not_recorded", "lactating", "dry")
OFFSPRING_AT_FOOT_STATES = ("not_recorded", "none", "single", "twins", "multiple")


class AnimalCohort(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    """A persistent, counted population that can move without losing its identity."""

    __tablename__ = "animal_cohorts"

    animal_group_type_id = db.Column(
        db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False, index=True
    )
    origin_farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=True, index=True)
    origin = db.Column(db.String(30), nullable=False, default="manual")
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    reproductive_state = db.Column(
        db.String(30), nullable=False, default="not_recorded", index=True
    )
    expected_litter_size = db.Column(
        db.String(20), nullable=False, default="not_recorded", index=True
    )
    lactation_state = db.Column(
        db.String(20), nullable=False, default="not_recorded", index=True
    )
    offspring_at_foot = db.Column(
        db.String(20), nullable=False, default="not_recorded", index=True
    )
    notes = db.Column(db.Text, nullable=True)

    animal_group_type = db.relationship("AnimalGroupType", back_populates="cohorts")
    origin_farm = db.relationship("Farm")
    balances = db.relationship("AnimalGroupBalance", back_populates="cohort")
    ledger_entries = db.relationship("StockLedgerEntry", back_populates="cohort")

    __table_args__ = (
        db.CheckConstraint(
            "origin IN ('legacy', 'manual', 'purchase', 'birth', 'split')",
            name="ck_animal_cohort_origin_allowed",
        ),
        db.CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_animal_cohort_status_allowed",
        ),
        db.CheckConstraint(
            "reproductive_state IN " + str(REPRODUCTIVE_STATES),
            name="ck_animal_cohort_reproductive_state_allowed",
        ),
        db.CheckConstraint(
            "expected_litter_size IN " + str(LITTER_SIZE_STATES),
            name="ck_animal_cohort_litter_size_allowed",
        ),
        db.CheckConstraint(
            "lactation_state IN " + str(LACTATION_STATES),
            name="ck_animal_cohort_lactation_state_allowed",
        ),
        db.CheckConstraint(
            "offspring_at_foot IN " + str(OFFSPRING_AT_FOOT_STATES),
            name="ck_animal_cohort_offspring_at_foot_allowed",
        ),
    )


class AnimalCohortLineage(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "animal_cohort_lineage"

    parent_cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=False, index=True
    )
    child_cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=False, index=True
    )
    movement_event_id = db.Column(
        db.String(36), db.ForeignKey("movement_events.id"), nullable=True, index=True
    )
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(30), nullable=False)

    parent_cohort = db.relationship("AnimalCohort", foreign_keys=[parent_cohort_id])
    child_cohort = db.relationship("AnimalCohort", foreign_keys=[child_cohort_id])
    movement_event = db.relationship("MovementEvent")

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_animal_cohort_lineage_quantity_positive"),
        db.CheckConstraint(
            "reason IN ('stock_split', 'reproductive_partition', 'correction')",
            name="ck_animal_cohort_lineage_reason_allowed",
        ),
        db.CheckConstraint(
            "parent_cohort_id <> child_cohort_id",
            name="ck_animal_cohort_lineage_distinct",
        ),
        db.UniqueConstraint(
            "parent_cohort_id", "child_cohort_id", name="uq_animal_cohort_lineage_edge"
        ),
    )
