from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Paddock(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "paddocks"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    area_ha = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    grazeable_area_ha = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="active")
    notes = db.Column(db.Text)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    stocking_rate_ha_per_lsu_override = db.Column(db.Numeric(8, 2), nullable=True)

    farm = db.relationship("Farm", back_populates="paddocks")
    grazing_allocations = db.relationship("GrazingAllocation", back_populates="paddock")
    events = db.relationship("PaddockEvent", back_populates="paddock", cascade="all, delete-orphan")
    water_assets = db.relationship("WaterAsset", back_populates="location_paddock")
    task_entity_links = db.relationship("TaskEntityLink", back_populates="paddock")
    gates_as_a = db.relationship(
        "PaddockGate",
        foreign_keys="PaddockGate.paddock_a_id",
        back_populates="paddock_a",
        cascade="all, delete-orphan",
    )
    gates_as_b = db.relationship(
        "PaddockGate",
        foreign_keys="PaddockGate.paddock_b_id",
        back_populates="paddock_b",
        cascade="all, delete-orphan",
    )
    served_water_links = db.relationship(
        "WaterAssetServedPaddock",
        back_populates="paddock",
        cascade="all, delete-orphan",
    )
    fence_sections_as_a = db.relationship(
        "FenceSection",
        foreign_keys="FenceSection.paddock_a_id",
        back_populates="paddock_a",
    )
    fence_sections_as_b = db.relationship(
        "FenceSection",
        foreign_keys="FenceSection.paddock_b_id",
        back_populates="paddock_b",
    )

    __table_args__ = (
        db.CheckConstraint(
            "stocking_rate_ha_per_lsu_override IS NULL OR stocking_rate_ha_per_lsu_override > 0",
            name="ck_paddock_stocking_rate_positive",
        ),
        db.UniqueConstraint("farm_id", "name", name="uq_paddock_farm_name"),
    )
