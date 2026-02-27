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


class GrazingAllocation(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "grazing_allocations"

    grazing_session_id = db.Column(
        db.String(36), db.ForeignKey("grazing_sessions.id"), nullable=False, index=True
    )
    paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    allocation_fraction = db.Column(db.Numeric(5, 4), nullable=False)

    grazing_session = db.relationship("GrazingSession", back_populates="allocations")
    paddock = db.relationship("Paddock", back_populates="grazing_allocations")

    __table_args__ = (
        db.CheckConstraint(
            "allocation_fraction > 0 and allocation_fraction <= 1", name="ck_allocation_fraction"
        ),
        db.UniqueConstraint("grazing_session_id", "paddock_id", name="uq_session_paddock"),
    )
