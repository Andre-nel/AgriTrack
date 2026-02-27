from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class RainfallRecord(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "rainfall_records"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    recorded_on = db.Column(db.Date, nullable=False, index=True)
    mm = db.Column(db.Numeric(8, 2), nullable=False)
    source = db.Column(db.String(30), nullable=False, default="manual")
    note = db.Column(db.Text)

    farm = db.relationship("Farm", back_populates="rainfall_records")

    __table_args__ = (db.CheckConstraint("mm >= 0", name="ck_rainfall_non_negative"),)
