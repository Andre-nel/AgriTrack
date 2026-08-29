from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class AnimalGroupType(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "animal_group_types"

    species = db.Column(db.String(50), nullable=False)
    breed = db.Column(db.String(50), nullable=False)
    sex = db.Column(db.String(10), nullable=False)
    age_class = db.Column(db.String(50), nullable=False)

    cohorts = db.relationship("AnimalCohort", back_populates="animal_group_type")

    __table_args__ = (
        db.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')",
            name="ck_animal_group_species_allowed",
        ),
        db.UniqueConstraint("species", "breed", "sex", "age_class", name="uq_animal_group_type"),
    )


class AnimalGroupBalance(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "animal_group_balances"

    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    animal_group_type_id = db.Column(
        db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False, index=True
    )
    cohort_id = db.Column(
        db.String(36), db.ForeignKey("animal_cohorts.id"), nullable=True, index=True
    )
    head_count = db.Column(db.Integer, nullable=False, default=0)

    mob = db.relationship("Mob", back_populates="balances")
    animal_group_type = db.relationship("AnimalGroupType")
    cohort = db.relationship("AnimalCohort", back_populates="balances")

    __table_args__ = (
        db.CheckConstraint("head_count >= 0", name="ck_balance_non_negative"),
        db.UniqueConstraint("mob_id", "cohort_id", name="uq_mob_cohort_balance"),
        db.Index(
            "uq_mob_legacy_group_balance",
            "mob_id",
            "animal_group_type_id",
            unique=True,
            sqlite_where=db.text("cohort_id IS NULL"),
            postgresql_where=db.text("cohort_id IS NULL"),
        ),
    )
