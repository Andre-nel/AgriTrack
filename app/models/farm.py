from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Farm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "farms"

    name = db.Column(db.String(120), nullable=False, unique=True)
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    active = db.Column(db.Boolean, nullable=False, default=True)

    paddocks = db.relationship("Paddock", back_populates="farm", cascade="all, delete-orphan")
    mobs = db.relationship("Mob", back_populates="farm", cascade="all, delete-orphan")
    rainfall_records = db.relationship(
        "RainfallRecord", back_populates="farm", cascade="all, delete-orphan"
    )
