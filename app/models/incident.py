from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Incident(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "incidents"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    occurred_on = db.Column(db.Date, nullable=False, index=True)
    category = db.Column(db.String(120), nullable=False, index=True)
    note = db.Column(db.Text, nullable=False)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    reported_by = db.Column(db.String(120), nullable=False)

    farm = db.relationship("Farm", back_populates="incidents")

