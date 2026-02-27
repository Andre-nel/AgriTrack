import enum

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class StockEventType(str, enum.Enum):
    birth = "birth"
    purchase = "purchase"
    transfer_in = "transfer_in"
    adjustment_in = "adjustment_in"
    death = "death"
    sale = "sale"
    missing = "missing"
    transfer_out = "transfer_out"
    adjustment_out = "adjustment_out"


class StockLedgerEntry(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "stock_ledger_entries"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    mob_id = db.Column(db.String(36), db.ForeignKey("mobs.id"), nullable=False, index=True)
    animal_group_type_id = db.Column(
        db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False, index=True
    )
    event_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    event_type = db.Column(db.Enum(StockEventType), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.Text)

    mob = db.relationship("Mob", back_populates="ledger_entries")

    __table_args__ = (db.CheckConstraint("quantity > 0", name="ck_stock_quantity_positive"),)
