import enum

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MovementEventKind(str, enum.Enum):
    move = "move"
    split = "split"
    merge = "merge"


class MovementRole(str, enum.Enum):
    source = "source"
    target = "target"
    result = "result"


class MovementEvent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "movement_events"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    event_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    event_kind = db.Column(db.Enum(MovementEventKind), nullable=False)
    source_note = db.Column(db.Text)

    mobs = db.relationship("MovementEventMob", back_populates="movement_event", cascade="all, delete-orphan")


class MovementEventMob(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "movement_event_mobs"

    movement_event_id = db.Column(
        db.String(36), db.ForeignKey("movement_events.id"), nullable=False, index=True
    )
    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    role = db.Column(db.Enum(MovementRole), nullable=False)

    movement_event = db.relationship("MovementEvent", back_populates="mobs")


class MobLineage(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "mob_lineage"

    parent_mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    child_mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    movement_event_id = db.Column(
        db.String(36), db.ForeignKey("movement_events.id"), nullable=False, index=True
    )
