from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class GrazingSession(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "grazing_sessions"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    start_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    end_at = db.Column(db.DateTime(timezone=True), index=True)

    mob = db.relationship("Mob", back_populates="grazing_sessions")
    allocations = db.relationship(
        "GrazingAllocation", back_populates="grazing_session", cascade="all, delete-orphan"
    )
    lsu_history = db.relationship(
        "GrazingAllocationLsuHistory",
        back_populates="grazing_session",
        cascade="all, delete-orphan",
    )


class GrazingAllocation(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "grazing_allocations"

    grazing_session_id = db.Column(
        db.String(36), db.ForeignKey("grazing_sessions.id"), nullable=False, index=True
    )
    paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    allocation_fraction = db.Column(db.Numeric(5, 4), nullable=False)

    grazing_session = db.relationship("GrazingSession", back_populates="allocations")
    paddock = db.relationship("Paddock", back_populates="grazing_allocations")
    lsu_history = db.relationship(
        "GrazingAllocationLsuHistory",
        back_populates="grazing_allocation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint(
            "allocation_fraction > 0 and allocation_fraction <= 1", name="ck_allocation_fraction"
        ),
        db.UniqueConstraint("grazing_session_id", "paddock_id", name="uq_session_paddock"),
    )


class GrazingAllocationLsuHistory(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "grazing_allocation_lsu_history"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    grazing_session_id = db.Column(
        db.String(36), db.ForeignKey("grazing_sessions.id"), nullable=False, index=True
    )
    grazing_allocation_id = db.Column(
        db.String(36), db.ForeignKey("grazing_allocations.id"), nullable=False, index=True
    )
    effective_from = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    effective_to = db.Column(db.DateTime(timezone=True), index=True)
    allocation_fraction = db.Column(db.Numeric(5, 4), nullable=False)
    mob_total_lsu = db.Column(db.Numeric(12, 4), nullable=False)
    allocated_lsu = db.Column(db.Numeric(12, 4), nullable=False)
    source = db.Column(db.String(20), nullable=False, default="live")

    farm = db.relationship("Farm")
    mob = db.relationship("Mob")
    paddock = db.relationship("Paddock")
    grazing_session = db.relationship("GrazingSession", back_populates="lsu_history")
    grazing_allocation = db.relationship("GrazingAllocation", back_populates="lsu_history")

    __table_args__ = (
        db.CheckConstraint(
            "source IN ('backfill_ledger', 'live')",
            name="ck_grazing_lsu_history_source",
        ),
        db.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_grazing_lsu_history_effective_range",
        ),
        db.CheckConstraint(
            "allocation_fraction > 0 and allocation_fraction <= 1",
            name="ck_grazing_lsu_history_fraction",
        ),
        db.CheckConstraint("mob_total_lsu >= 0", name="ck_grazing_lsu_history_mob_total_non_negative"),
        db.CheckConstraint("allocated_lsu >= 0", name="ck_grazing_lsu_history_allocated_non_negative"),
        db.UniqueConstraint(
            "grazing_allocation_id",
            "effective_from",
            name="uq_grazing_lsu_history_allocation_effective_from",
        ),
    )
