import uuid

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class TimestampMixin:
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at = db.Column(DateTime(timezone=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class UUIDPrimaryKeyMixin:
    id = db.Column(
        UUID(as_uuid=True).with_variant(db.String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
    )
