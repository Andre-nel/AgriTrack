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


class LivestockTrade(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "livestock_trades"

    trade_date = db.Column(db.Date, nullable=False, index=True)
    trade_type = db.Column(db.String(20), nullable=False, index=True)
    counterparty = db.Column(db.String(120), nullable=False)
    reference = db.Column(db.String(120), nullable=True)
    vat_rate = db.Column(db.Numeric(7, 4), nullable=False, default=0.15)
    species = db.Column(db.String(20), nullable=False, index=True)
    pricing_model = db.Column(db.String(20), nullable=False)
    import_id = db.Column(db.String(36), nullable=True, unique=True)
    import_payload_hash = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    cash_transaction_id = db.Column(
        db.String(36),
        db.ForeignKey("cash_transactions.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    cash_transaction = db.relationship("CashTransaction")
    farm_links = db.relationship(
        "LivestockTradeFarm",
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="LivestockTradeFarm.sort_order",
    )
    groups = db.relationship(
        "LivestockTradeGroup",
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="LivestockTradeGroup.sort_order",
    )
    attachments = db.relationship(
        "LivestockTradeAttachment",
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="LivestockTradeAttachment.sort_order",
    )

    __table_args__ = (
        db.CheckConstraint("trade_type IN ('sale', 'purchase')", name="ck_livestock_trade_type_allowed"),
        db.CheckConstraint(
            "pricing_model IN ('weight', 'head')",
            name="ck_livestock_trade_pricing_model_allowed",
        ),
        db.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="ck_livestock_trade_vat_range"),
        db.CheckConstraint(
            "species IN ('Cattle', 'Sheep', 'Goat')", name="ck_livestock_trade_species",
        ),
    )


class LivestockTradeAttachment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "livestock_trade_attachments"

    livestock_trade_id = db.Column(
        db.String(36), db.ForeignKey("livestock_trades.id"), nullable=False, index=True,
    )
    sort_order = db.Column(db.Integer, nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)

    trade = db.relationship("LivestockTrade", back_populates="attachments")

    __table_args__ = (
        db.CheckConstraint("byte_size > 0", name="ck_livestock_trade_attachment_size_positive"),
        db.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_attachment_order"),
        db.UniqueConstraint("livestock_trade_id", "sort_order", name="uq_livestock_trade_attachment_order"),
    )


class LivestockTradeGroup(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "livestock_trade_groups"

    livestock_trade_id = db.Column(db.String(36), db.ForeignKey("livestock_trades.id"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(120), nullable=True)
    animal_group_type_id = db.Column(db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False, index=True)
    price_per_kg = db.Column(db.Numeric(12, 4), nullable=True)
    price_per_head = db.Column(db.Numeric(12, 2), nullable=True)
    head_count = db.Column(db.Integer, nullable=True)
    hair_lengths = db.Column(db.String(250), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    trade = db.relationship("LivestockTrade", back_populates="groups")
    animal_group_type = db.relationship("AnimalGroupType")
    weights = db.relationship("LivestockTradeWeight", back_populates="group", cascade="all, delete-orphan", order_by="LivestockTradeWeight.sequence")

    __table_args__ = (
        db.UniqueConstraint("livestock_trade_id", "sort_order", name="uq_livestock_trade_group_order"),
        db.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_group_order"),
        db.CheckConstraint(
            "(price_per_kg IS NOT NULL AND price_per_kg > 0 AND price_per_head IS NULL AND head_count IS NULL) OR "
            "(price_per_kg IS NULL AND price_per_head IS NOT NULL AND price_per_head > 0 AND head_count IS NOT NULL AND head_count > 0)",
            name="ck_livestock_trade_group_pricing",
        ),
    )


class LivestockTradeFarm(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "livestock_trade_farms"

    livestock_trade_id = db.Column(
        db.String(36),
        db.ForeignKey("livestock_trades.id"),
        nullable=False,
        index=True,
    )
    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    trade = db.relationship("LivestockTrade", back_populates="farm_links")
    farm = db.relationship("Farm")

    __table_args__ = (
        db.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_farm_sort_non_negative"),
        db.UniqueConstraint(
            "livestock_trade_id",
            "farm_id",
            name="uq_livestock_trade_farm",
        ),
    )


class LivestockTradeWeight(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "livestock_trade_weights"

    group_id = db.Column(
        db.String(36),
        db.ForeignKey("livestock_trade_groups.id"),
        nullable=False,
        index=True,
    )
    sequence = db.Column(db.Integer, nullable=False)
    weight_kg = db.Column(db.Numeric(10, 3), nullable=False)

    group = db.relationship("LivestockTradeGroup", back_populates="weights")

    __table_args__ = (
        db.CheckConstraint("sequence >= 0", name="ck_livestock_trade_weight_sequence_non_negative"),
        db.CheckConstraint("weight_kg > 0", name="ck_livestock_trade_weight_positive"),
        db.UniqueConstraint(
            "group_id",
            "sequence",
            name="uq_livestock_trade_weight_sequence",
        ),
    )
