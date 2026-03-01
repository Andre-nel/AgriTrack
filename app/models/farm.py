from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Farm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "farms"

    name = db.Column(db.String(120), nullable=False, unique=True)
    timezone = db.Column(db.String(64), nullable=False, default="UTC")
    active = db.Column(db.Boolean, nullable=False, default=True)
    default_stocking_rate_ha_per_lsu = db.Column(db.Numeric(8, 2), nullable=False, default=6)

    paddocks = db.relationship("Paddock", back_populates="farm", cascade="all, delete-orphan")
    mobs = db.relationship("Mob", back_populates="farm", cascade="all, delete-orphan")
    rainfall_records = db.relationship(
        "RainfallRecord", back_populates="farm", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint(
            "default_stocking_rate_ha_per_lsu > 0",
            name="ck_farm_stocking_rate_positive",
        ),
    )
