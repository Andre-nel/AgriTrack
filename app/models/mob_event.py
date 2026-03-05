from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MobEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "mob_events"

    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    event_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    description = db.Column(db.Text, nullable=False)

    mob = db.relationship("Mob", back_populates="events")
