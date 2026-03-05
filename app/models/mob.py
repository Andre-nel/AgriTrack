from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Mob(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "mobs"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    origin_note = db.Column(db.Text)

    farm = db.relationship("Farm", back_populates="mobs")
    balances = db.relationship("AnimalGroupBalance", back_populates="mob", cascade="all, delete-orphan")
    ledger_entries = db.relationship("StockLedgerEntry", back_populates="mob", cascade="all, delete-orphan")
    grazing_sessions = db.relationship("GrazingSession", back_populates="mob", cascade="all, delete-orphan")
    events = db.relationship("MobEvent", back_populates="mob", cascade="all, delete-orphan")

    __table_args__ = (
        db.Index(
            "uq_mob_farm_name_active",
            "farm_id",
            "name",
            unique=True,
            sqlite_where=db.text("status = 'active'"),
            postgresql_where=db.text("status = 'active'"),
        ),
    )
