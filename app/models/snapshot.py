from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DailyStockSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "daily_stock_snapshots"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    snapshot_date = db.Column(db.Date, nullable=False, index=True)
    paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=True, index=True)
    animal_group_type_id = db.Column(
        db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False, index=True
    )
    head_count = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint(
            "farm_id",
            "snapshot_date",
            "paddock_id",
            "animal_group_type_id",
            name="uq_daily_stock_snapshot",
        ),
    )
