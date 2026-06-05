from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class WaterAssetEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "water_asset_events"

    water_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        nullable=False,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    event_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    description = db.Column(db.Text, nullable=False)

    water_asset = db.relationship("WaterAsset", back_populates="events")
    attachments = db.relationship(
        "NoteAttachment",
        back_populates="water_asset_event",
        cascade="all, delete-orphan",
    )
