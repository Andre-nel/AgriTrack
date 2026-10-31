"""Orchard layout uses local metres, never geographical coordinates."""

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Orchard(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchards"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    revision = db.Column(db.Integer, nullable=False, default=1)
    seed_key = db.Column(db.String(100))
    farm = db.relationship("Farm", back_populates="orchards")

    __table_args__ = (
        db.UniqueConstraint("farm_id", "seed_key", name="uq_orchard_seed"),
        db.CheckConstraint("revision > 0", name="ck_orchard_revision"),
    )


class OrchardRow(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchard_rows"

    orchard_id = db.Column(db.String(36), db.ForeignKey("orchards.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    orientation = db.Column(db.String(10), nullable=False)
    x_m = db.Column(db.Float, nullable=False)
    y_m = db.Column(db.Float, nullable=False)
    spacing_m = db.Column(db.Float, nullable=False)
    # Null coordinates keep the label at the automatically calculated row start.
    label_x_m = db.Column(db.Float)
    label_y_m = db.Column(db.Float)

    __table_args__ = (
        db.CheckConstraint("orientation IN ('horizontal', 'vertical')", name="ck_orchard_row_orientation"),
        db.CheckConstraint("spacing_m > 0", name="ck_orchard_row_spacing"),
    )


class OrchardSpace(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchard_spaces"

    row_id = db.Column(db.String(36), db.ForeignKey("orchard_rows.id", ondelete="CASCADE"), nullable=False, index=True)
    # Signed indices allow extending either end without renumbering stable spaces.
    position = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.UniqueConstraint("row_id", "position", name="uq_orchard_space_position"),)


class OrchardPlant(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchard_plants"

    orchard_id = db.Column(db.String(36), db.ForeignKey("orchards.id", ondelete="CASCADE"), nullable=False, index=True)
    space_id = db.Column(db.String(36), db.ForeignKey("orchard_spaces.id", ondelete="RESTRICT"), unique=True)
    name = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="unassigned")
    purchased_on = db.Column(db.Date)
    supplier = db.Column(db.String(160))
    planted_on = db.Column(db.Date)

    __table_args__ = (
        db.CheckConstraint(
            "(status = 'unassigned' AND space_id IS NULL) OR "
            "(status IN ('planned', 'planted') AND space_id IS NOT NULL)",
            name="ck_orchard_plant_placement",
        ),
        db.CheckConstraint("planted_on IS NULL OR status = 'planted'", name="ck_orchard_plant_date"),
    )


class OrchardLine(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchard_lines"

    orchard_id = db.Column(db.String(36), db.ForeignKey("orchards.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    vertices = db.Column(db.JSON, nullable=False)
    schematic = db.Column(db.Boolean, nullable=False, default=False)


class OrchardEmitter(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "orchard_emitters"

    line_id = db.Column(db.String(36), db.ForeignKey("orchard_lines.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False, default="dripper")
    x_m = db.Column(db.Float, nullable=False)
    y_m = db.Column(db.Float, nullable=False)

    __table_args__ = (db.CheckConstraint("kind IN ('dripper', 'sprinkler')", name="ck_orchard_emitter_kind"),)
