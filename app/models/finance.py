from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class CashTransaction(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "cash_transactions"

    transaction_date = db.Column(db.Date, nullable=False, index=True)
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=True, index=True)
    reference = db.Column(db.String(120), nullable=True)
    counterparty = db.Column(db.String(120), nullable=True)
    description = db.Column(db.Text, nullable=False)

    farm = db.relationship("Farm")
    lines = db.relationship(
        "CashTransactionLine",
        back_populates="transaction",
        cascade="all, delete-orphan",
    )


class CashTransactionLine(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "cash_transaction_lines"

    transaction_id = db.Column(
        db.String(36),
        db.ForeignKey("cash_transactions.id"),
        nullable=False,
        index=True,
    )
    category_code = db.Column(db.String(60), nullable=False, index=True)
    direction = db.Column(db.String(10), nullable=False)
    species_scope = db.Column(db.String(20), nullable=True, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    source_type = db.Column(db.String(50), nullable=True)
    source_id = db.Column(db.String(64), nullable=True)

    transaction = db.relationship("CashTransaction", back_populates="lines")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_cash_transaction_lines_amount_positive"),
        db.CheckConstraint(
            "direction IN ('inflow', 'outflow')",
            name="ck_cash_transaction_lines_direction_allowed",
        ),
        db.CheckConstraint(
            "species_scope IS NULL OR species_scope IN ('Sheep', 'Cattle', 'Goat')",
            name="ck_cash_transaction_lines_species_scope_allowed",
        ),
    )
