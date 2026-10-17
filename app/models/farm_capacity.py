from datetime import datetime, timezone

from app.extensions import db


class FarmCapacityHistory(db.Model):
    """Observed farm capacity, ordered by effective time and insertion sequence."""

    __tablename__ = "farm_capacity_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    farm_id = db.Column(
        db.String(36), db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    recorded_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    capacity_lsu = db.Column(db.Numeric(18, 6), nullable=False)
    source = db.Column(db.String(20), nullable=False)

    farm = db.relationship("Farm")

    __table_args__ = (
        db.CheckConstraint(
            "source IN ('baseline', 'created', 'change')", name="ck_farm_capacity_history_source"
        ),
    )
