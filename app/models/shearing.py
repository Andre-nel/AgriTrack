from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Shearer(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearers"

    name = db.Column(db.String(120), nullable=False)
    name_key = db.Column(db.String(120), nullable=False, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    entries = db.relationship("ShearingEntry", back_populates="shearer")


class ShearingSession(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearing_sessions"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    species = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date(), nullable=False, index=True)
    end_date = db.Column(db.Date(), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    lootjie_rate = db.Column(db.Numeric(10, 2), nullable=False)
    adult_old_ram_multiplier = db.Column(db.Numeric(5, 2), nullable=False, default=2)
    notes = db.Column(db.Text, nullable=True)

    farm = db.relationship("Farm", back_populates="shearing_sessions")
    entries = db.relationship(
        "ShearingEntry",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    bales = db.relationship(
        "ShearingBale",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    attachments = db.relationship(
        "ShearingSessionAttachment",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint("species IN ('Sheep', 'Goat')", name="ck_shearing_session_species_allowed"),
        db.CheckConstraint("status IN ('open', 'closed')", name="ck_shearing_session_status_allowed"),
        db.CheckConstraint("lootjie_rate >= 0", name="ck_shearing_session_lootjie_non_negative"),
        db.CheckConstraint(
            "adult_old_ram_multiplier >= 1",
            name="ck_shearing_session_ram_multiplier_minimum",
        ),
        db.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_shearing_session_date_order",
        ),
    )


class ShearingEntry(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearing_entries"

    session_id = db.Column(
        db.String(36),
        db.ForeignKey("shearing_sessions.id"),
        nullable=False,
        index=True,
    )
    work_date = db.Column(db.Date(), nullable=False, index=True)
    shearer_id = db.Column(db.String(36), db.ForeignKey("shearers.id"), nullable=False, index=True)
    animal_group_type_id = db.Column(
        db.String(36),
        db.ForeignKey("animal_group_types.id"),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False)
    unit_rate_override = db.Column(db.Numeric(10, 2), nullable=True)
    line_amount_override = db.Column(db.Numeric(12, 2), nullable=True)
    note = db.Column(db.Text, nullable=True)

    session = db.relationship("ShearingSession", back_populates="entries")
    shearer = db.relationship("Shearer", back_populates="entries")
    animal_group_type = db.relationship("AnimalGroupType")

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_shearing_entry_quantity_positive"),
        db.CheckConstraint(
            "unit_rate_override IS NULL OR unit_rate_override >= 0",
            name="ck_shearing_entry_rate_override_non_negative",
        ),
        db.CheckConstraint(
            "line_amount_override IS NULL OR line_amount_override >= 0",
            name="ck_shearing_entry_amount_override_non_negative",
        ),
        db.UniqueConstraint(
            "session_id",
            "work_date",
            "shearer_id",
            "animal_group_type_id",
            name="uq_shearing_entry_daily_shearer_group",
        ),
    )


class ShearingBaleCode(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearing_bale_codes"

    species = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.String(60), nullable=False)
    code_key = db.Column(db.String(60), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    line_type = db.Column(db.String(80), nullable=True)
    age_group = db.Column(db.String(80), nullable=True)
    fineness_grade = db.Column(db.String(80), nullable=True)
    length_code = db.Column(db.String(20), nullable=True)
    fineness_micron = db.Column(db.Numeric(8, 2), nullable=True)
    clean_yield_percent = db.Column(db.Numeric(6, 2), nullable=True)
    style_character = db.Column(db.String(120), nullable=True)
    consistency = db.Column(db.String(120), nullable=True)
    color = db.Column(db.String(80), nullable=True)
    vegetable_matter = db.Column(db.String(80), nullable=True)
    fault = db.Column(db.String(120), nullable=True)
    description = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    bales = db.relationship("ShearingBale", back_populates="bale_code")

    __table_args__ = (
        db.CheckConstraint("species IN ('Sheep', 'Goat')", name="ck_shearing_bale_code_species_allowed"),
        db.UniqueConstraint("species", "code_key", name="uq_shearing_bale_code_species_code_key"),
    )


class ShearingBale(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearing_bales"

    session_id = db.Column(
        db.String(36),
        db.ForeignKey("shearing_sessions.id"),
        nullable=False,
        index=True,
    )
    bale_code_id = db.Column(
        db.String(36),
        db.ForeignKey("shearing_bale_codes.id"),
        nullable=True,
        index=True,
    )
    code_text = db.Column(db.String(60), nullable=False)
    code_key = db.Column(db.String(60), nullable=False, index=True)
    bale_number = db.Column(db.String(60), nullable=True)
    weight_kg = db.Column(db.Numeric(10, 3), nullable=False)
    price_per_kg = db.Column(db.Numeric(12, 4), nullable=True)
    total_price = db.Column(db.Numeric(12, 2), nullable=True)
    pricing_input_mode = db.Column(db.String(20), nullable=False, default="unpriced")
    notes = db.Column(db.Text, nullable=True)

    session = db.relationship("ShearingSession", back_populates="bales")
    bale_code = db.relationship("ShearingBaleCode", back_populates="bales")

    __table_args__ = (
        db.CheckConstraint("weight_kg > 0", name="ck_shearing_bale_weight_positive"),
        db.CheckConstraint(
            "price_per_kg IS NULL OR price_per_kg >= 0",
            name="ck_shearing_bale_price_per_kg_non_negative",
        ),
        db.CheckConstraint(
            "total_price IS NULL OR total_price >= 0",
            name="ck_shearing_bale_total_price_non_negative",
        ),
        db.CheckConstraint(
            "pricing_input_mode IN ('unpriced', 'price_per_kg', 'total_price', 'both')",
            name="ck_shearing_bale_pricing_input_mode",
        ),
    )


class ShearingSessionAttachment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "shearing_session_attachments"

    session_id = db.Column(
        db.String(36),
        db.ForeignKey("shearing_sessions.id"),
        nullable=False,
        index=True,
    )
    uploaded_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=False)
    byte_size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    caption = db.Column(db.Text, nullable=True)
    storage_path = db.Column(db.String(500), nullable=False)

    session = db.relationship("ShearingSession", back_populates="attachments")
    uploaded_by = db.relationship("User")

    __table_args__ = (
        db.CheckConstraint("byte_size >= 0", name="ck_shearing_session_attachments_byte_size_non_negative"),
    )
