from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PaddockGate(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "paddock_gates"

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    SOURCE_AUTO = "auto"
    SOURCE_MANUAL = "manual"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    paddock_a_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    paddock_b_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_CLOSED)
    active = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(20), nullable=False, default=SOURCE_MANUAL)
    latitude = db.Column(db.Numeric(11, 8), nullable=True)
    longitude = db.Column(db.Numeric(12, 8), nullable=True)
    shared_boundary_length_m = db.Column(db.Numeric(12, 2), nullable=True)
    last_state_changed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    farm = db.relationship("Farm", back_populates="paddock_gates")
    paddock_a = db.relationship(
        "Paddock",
        foreign_keys=[paddock_a_id],
        back_populates="gates_as_a",
    )
    paddock_b = db.relationship(
        "Paddock",
        foreign_keys=[paddock_b_id],
        back_populates="gates_as_b",
    )

    __table_args__ = (
        db.CheckConstraint("paddock_a_id < paddock_b_id", name="ck_paddock_gate_sorted_ids"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_paddock_gate_status"),
        db.CheckConstraint("source IN ('auto', 'manual')", name="ck_paddock_gate_source"),
    )
