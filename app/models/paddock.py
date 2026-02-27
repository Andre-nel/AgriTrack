from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Paddock(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "paddocks"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    area_ha = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    grazeable_area_ha = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="active")

    farm = db.relationship("Farm", back_populates="paddocks")
    grazing_allocations = db.relationship("GrazingAllocation", back_populates="paddock")

    __table_args__ = (db.UniqueConstraint("farm_id", "name", name="uq_paddock_farm_name"),)
