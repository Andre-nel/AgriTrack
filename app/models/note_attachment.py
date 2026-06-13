from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class NoteAttachment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "note_attachments"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    mob_event_id = db.Column(db.String(36), db.ForeignKey("mob_events.id"), index=True)
    paddock_event_id = db.Column(db.String(36), db.ForeignKey("paddock_events.id"), index=True)
    water_asset_event_id = db.Column(
        db.String(36),
        db.ForeignKey("water_asset_events.id"),
        index=True,
    )
    fence_event_id = db.Column(db.String(36), db.ForeignKey("fence_events.id"), index=True)
    uploaded_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    client_attachment_id = db.Column(db.String(120), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    caption = db.Column(db.Text)
    captured_at = db.Column(db.DateTime(timezone=True))
    storage_path = db.Column(db.String(500), nullable=False)

    farm = db.relationship("Farm")
    uploaded_by = db.relationship("User")
    mob_event = db.relationship("MobEvent", back_populates="attachments")
    paddock_event = db.relationship("PaddockEvent", back_populates="attachments")
    water_asset_event = db.relationship("WaterAssetEvent", back_populates="attachments")
    fence_event = db.relationship("FenceEvent", back_populates="attachments")

    __table_args__ = (
        db.CheckConstraint(
            (
                "(mob_event_id IS NOT NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL AND fence_event_id IS NULL) "
                "OR (mob_event_id IS NULL AND paddock_event_id IS NOT NULL AND water_asset_event_id IS NULL AND fence_event_id IS NULL) "
                "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NOT NULL AND fence_event_id IS NULL) "
                "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL AND fence_event_id IS NOT NULL)"
            ),
            name="ck_note_attachments_one_event",
        ),
        db.CheckConstraint("byte_size >= 0", name="ck_note_attachments_byte_size_non_negative"),
        db.UniqueConstraint(
            "uploaded_by_user_id",
            "client_attachment_id",
            name="uq_note_attachments_user_client_attachment",
        ),
    )
