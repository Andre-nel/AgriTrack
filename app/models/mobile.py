from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MobileAuthToken(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "mobile_auth_tokens"

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    token_prefix = db.Column(db.String(12), nullable=False, index=True)
    device_name = db.Column(db.String(120), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True), index=True)
    last_used_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", back_populates="mobile_tokens")


class MobileSyncCommand(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "mobile_sync_commands"

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    client_command_id = db.Column(db.String(120), nullable=False)
    command_type = db.Column(db.String(80), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="applied", index=True)
    request_payload = db.Column(db.JSON, nullable=False)
    response_payload = db.Column(db.JSON)
    error_code = db.Column(db.String(80))
    error_message = db.Column(db.Text)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)

    user = db.relationship("User", back_populates="mobile_sync_commands")
    farm = db.relationship("Farm")

    __table_args__ = (
        db.UniqueConstraint("user_id", "client_command_id", name="uq_mobile_sync_user_command"),
        db.CheckConstraint(
            "status IN ('applied', 'failed')",
            name="ck_mobile_sync_command_status",
        ),
    )
