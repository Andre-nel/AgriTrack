from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class FenceSection(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "fence_sections"

    SOURCE_AUTO = "auto"
    SOURCE_MANUAL = "manual"
    TYPE_BOUNDARY = "boundary"
    TYPE_INTERNAL = "internal"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    section_key = db.Column(db.String(160), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    source = db.Column(db.String(20), nullable=False, default=SOURCE_MANUAL)
    section_type = db.Column(db.String(20), nullable=False)
    paddock_a_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), nullable=False, index=True)
    paddock_b_id = db.Column(db.String(36), db.ForeignKey("paddocks.id"), index=True)
    length_m = db.Column(db.Numeric(12, 2))
    geometry_json = db.Column(db.Text)

    condition = db.Column(db.String(20), nullable=False, default="unknown", index=True)
    height_profile = db.Column(db.String(20), nullable=False, default="low")
    construction_type = db.Column(db.String(40), nullable=False, default="high_strung_wire")
    post_type = db.Column(db.String(40), nullable=False, default="unknown")
    dropper_type = db.Column(db.String(40), nullable=False, default="unknown")
    wire_type = db.Column(db.String(40), nullable=False, default="high_strung")
    mesh_type = db.Column(db.String(40), nullable=False, default="none")
    electric_wire = db.Column(db.Boolean, nullable=False, default=False)
    electric_wire_type = db.Column(db.String(80))
    notes = db.Column(db.Text)
    tags_csv = db.Column(db.String(500), nullable=False, default="")

    holds_cattle = db.Column(db.String(20), nullable=False, default="unknown")
    holds_sheep = db.Column(db.String(20), nullable=False, default="unknown")
    holds_goats = db.Column(db.String(20), nullable=False, default="unknown")
    excludes_jackal = db.Column(db.String(20), nullable=False, default="unknown")
    excludes_predators = db.Column(db.String(20), nullable=False, default="unknown")

    farm = db.relationship("Farm", back_populates="fence_sections")
    paddock_a = db.relationship(
        "Paddock",
        foreign_keys=[paddock_a_id],
        back_populates="fence_sections_as_a",
    )
    paddock_b = db.relationship(
        "Paddock",
        foreign_keys=[paddock_b_id],
        back_populates="fence_sections_as_b",
    )
    events = db.relationship("FenceEvent", back_populates="fence_section", cascade="all, delete-orphan")
    task_entity_links = db.relationship("TaskEntityLink", back_populates="fence_section")

    __table_args__ = (
        db.CheckConstraint("source IN ('auto', 'manual')", name="ck_fence_sections_source"),
        db.CheckConstraint("section_type IN ('boundary', 'internal')", name="ck_fence_sections_type"),
        db.CheckConstraint(
            (
                "(section_type = 'boundary' AND paddock_b_id IS NULL) "
                "OR (section_type = 'internal' AND paddock_b_id IS NOT NULL AND paddock_a_id < paddock_b_id)"
            ),
            name="ck_fence_sections_paddock_shape",
        ),
        db.UniqueConstraint("farm_id", "section_key", name="uq_fence_sections_farm_key"),
    )


class FenceEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "fence_events"

    fence_section_id = db.Column(db.String(36), db.ForeignKey("fence_sections.id"), nullable=False, index=True)
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    event_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    event_type = db.Column(db.String(30), nullable=False, default="note", index=True)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    condition_after = db.Column(db.String(20))
    description = db.Column(db.Text, nullable=False)

    fence_section = db.relationship("FenceSection", back_populates="events")
    farm = db.relationship("Farm")
    materials = db.relationship(
        "FenceEventMaterial",
        back_populates="event",
        cascade="all, delete-orphan",
    )
    attachments = db.relationship(
        "NoteAttachment",
        back_populates="fence_event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint("event_type IN ('note', 'inspection', 'maintenance')", name="ck_fence_events_type"),
    )


class FenceEventMaterial(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "fence_event_materials"

    event_id = db.Column(db.String(36), db.ForeignKey("fence_events.id"), nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False)
    material_type = db.Column(db.String(40), nullable=False)
    material_detail = db.Column(db.String(160))
    quantity = db.Column(db.Numeric(12, 2))
    unit = db.Column(db.String(30))
    notes = db.Column(db.Text)

    event = db.relationship("FenceEvent", back_populates="materials")

    __table_args__ = (
        db.CheckConstraint(
            "action IN ('replaced', 'installed', 'repaired', 'packed', 'removed')",
            name="ck_fence_event_materials_action",
        ),
        db.CheckConstraint(
            "material_type IN ('mesh', 'wire', 'pole', 'dropper', 'electric_wire', 'stone', 'other')",
            name="ck_fence_event_materials_type",
        ),
        db.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_fence_event_materials_quantity_non_negative",
        ),
    )
