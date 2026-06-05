from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


WATER_ASSET_TYPES = (
    "borehole",
    "pit",
    "windmill",
    "solarpump",
    "cement_dam",
    "tank",
    "ground_dam",
    "weir",
    "trough",
)
WATER_CONNECTION_FLOW_TYPES = ("pumped", "gravity")


class WaterAsset(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "water_assets"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    asset_type = db.Column(db.String(30), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    needs_review = db.Column(db.Boolean, nullable=False, default=False)
    location_paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), index=True)
    latitude = db.Column(db.Numeric(10, 7))
    longitude = db.Column(db.Numeric(11, 7))
    altitude_m = db.Column(db.Numeric(10, 2))
    status = db.Column(db.String(40))
    water_level = db.Column(db.String(30))
    capacity_m3 = db.Column(db.Numeric(12, 2))
    material = db.Column(db.String(20))
    windmill_size_ft = db.Column(db.Integer)
    solar_brand = db.Column(db.String(120))
    solar_kw = db.Column(db.Numeric(8, 2))
    solar_head_m = db.Column(db.Numeric(8, 2))
    weir_size = db.Column(db.String(20))
    trough_size = db.Column(db.String(20))
    source_system = db.Column(db.String(120))
    import_placemark_name = db.Column(db.String(120))
    import_style_url = db.Column(db.String(255))

    farm = db.relationship("Farm", back_populates="water_assets")
    location_paddock = db.relationship("Paddock", back_populates="water_assets")
    events = db.relationship("WaterAssetEvent", back_populates="water_asset", cascade="all, delete-orphan")
    state_history = db.relationship(
        "WaterAssetStateHistory",
        back_populates="water_asset",
        cascade="all, delete-orphan",
    )
    task_entity_links = db.relationship("TaskEntityLink", back_populates="water_asset")
    served_paddock_links = db.relationship(
        "WaterAssetServedPaddock",
        back_populates="water_asset",
        cascade="all, delete-orphan",
    )
    source_connections = db.relationship(
        "WaterConnection",
        foreign_keys="WaterConnection.source_asset_id",
        back_populates="source_asset",
        overlaps="destination_asset,destination_connections,pump_asset,pump_connections",
    )
    destination_connections = db.relationship(
        "WaterConnection",
        foreign_keys="WaterConnection.destination_asset_id",
        back_populates="destination_asset",
        overlaps="source_asset,source_connections,pump_asset,pump_connections",
    )
    pump_connections = db.relationship(
        "WaterConnection",
        foreign_keys="WaterConnection.pump_asset_id",
        back_populates="pump_asset",
        overlaps="source_asset,source_connections,destination_asset,destination_connections",
    )

    __table_args__ = (
        db.CheckConstraint(
            "asset_type IN ('borehole', 'pit', 'windmill', 'solarpump', 'cement_dam', "
            "'tank', 'ground_dam', 'weir', 'trough')",
            name="ck_water_asset_type",
        ),
        db.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_water_asset_latitude_range",
        ),
        db.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_water_asset_longitude_range",
        ),
        db.CheckConstraint(
            "capacity_m3 IS NULL OR capacity_m3 >= 0",
            name="ck_water_asset_capacity_non_negative",
        ),
        db.CheckConstraint(
            "solar_kw IS NULL OR solar_kw >= 0",
            name="ck_water_asset_solar_kw_non_negative",
        ),
        db.CheckConstraint(
            "solar_head_m IS NULL OR solar_head_m >= 0",
            name="ck_water_asset_solar_head_non_negative",
        ),
        db.CheckConstraint(
            "windmill_size_ft IS NULL OR windmill_size_ft IN (10, 12, 14)",
            name="ck_water_asset_windmill_size",
        ),
        db.CheckConstraint(
            "weir_size IS NULL OR weir_size IN ('small', 'medium', 'large')",
            name="ck_water_asset_weir_size",
        ),
    )


class WaterAssetStateHistory(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "water_asset_state_history"

    water_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        nullable=False,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    change_type = db.Column(db.String(20), nullable=False, default="updated", index=True)
    changed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    previous_active = db.Column(db.Boolean)
    previous_status = db.Column(db.String(40))
    previous_water_level = db.Column(db.String(30))
    active = db.Column(db.Boolean, nullable=False)
    status = db.Column(db.String(40))
    water_level = db.Column(db.String(30))

    water_asset = db.relationship("WaterAsset", back_populates="state_history")
    farm = db.relationship("Farm")

    __table_args__ = (
        db.CheckConstraint(
            "change_type IN ('baseline', 'created', 'updated')",
            name="ck_water_asset_state_history_change_type",
        ),
        db.Index(
            "ix_water_asset_state_history_asset_changed_at",
            "water_asset_id",
            "changed_at",
        ),
    )


class WaterAssetServedPaddock(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "water_asset_served_paddocks"

    water_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        nullable=False,
        index=True,
    )
    paddock_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)

    water_asset = db.relationship("WaterAsset", back_populates="served_paddock_links")
    paddock = db.relationship("Paddock", back_populates="served_water_links")

    __table_args__ = (
        db.UniqueConstraint("water_asset_id", "paddock_id", name="uq_water_asset_served_paddock"),
    )


class WaterConnection(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "water_connections"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    flow_type = db.Column(db.String(20), nullable=False)
    source_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        nullable=False,
        index=True,
    )
    destination_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        nullable=False,
        index=True,
    )
    pump_asset_id = db.Column(
        db.String(36),
        db.ForeignKey("water_assets.id"),
        index=True,
    )
    pipe_material = db.Column(db.String(20))
    pipe_diameter_spec = db.Column(db.String(50))
    pipe_wall_thickness_spec = db.Column(db.String(50))
    pipe_class_spec = db.Column(db.String(50))
    pipe_quality_spec = db.Column(db.String(50))
    notes = db.Column(db.Text)

    farm = db.relationship("Farm", back_populates="water_connections")
    source_asset = db.relationship(
        "WaterAsset",
        foreign_keys=[source_asset_id],
        back_populates="source_connections",
        overlaps="destination_asset,destination_connections,pump_asset,pump_connections",
    )
    destination_asset = db.relationship(
        "WaterAsset",
        foreign_keys=[destination_asset_id],
        back_populates="destination_connections",
        overlaps="source_asset,source_connections,pump_asset,pump_connections",
    )
    pump_asset = db.relationship(
        "WaterAsset",
        foreign_keys=[pump_asset_id],
        back_populates="pump_connections",
        overlaps="source_asset,source_connections,destination_asset,destination_connections",
    )

    __table_args__ = (
        db.CheckConstraint(
            "flow_type IN ('pumped', 'gravity')",
            name="ck_water_connection_flow_type",
        ),
        db.CheckConstraint(
            "source_asset_id <> destination_asset_id",
            name="ck_water_connection_distinct_assets",
        ),
    )
