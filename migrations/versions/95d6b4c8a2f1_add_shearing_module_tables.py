"""Add shearing module tables.

Revision ID: 95d6b4c8a2f1
Revises: 4e7a1c9b2d30
Create Date: 2026-07-01 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "95d6b4c8a2f1"
down_revision = "4e7a1c9b2d30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shearers",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("name_key", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key", name="uq_shearer_name_key"),
    )

    op.create_table(
        "shearing_sessions",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("species", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lootjie_rate", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("adult_old_ram_multiplier", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("adult_old_ram_multiplier >= 1", name="ck_shearing_session_ram_multiplier_minimum"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_shearing_session_date_order"),
        sa.CheckConstraint("lootjie_rate >= 0", name="ck_shearing_session_lootjie_non_negative"),
        sa.CheckConstraint("species IN ('Sheep', 'Goat')", name="ck_shearing_session_species_allowed"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_shearing_session_status_allowed"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shearing_sessions_end_date"), "shearing_sessions", ["end_date"], unique=False)
    op.create_index(op.f("ix_shearing_sessions_farm_id"), "shearing_sessions", ["farm_id"], unique=False)
    op.create_index(op.f("ix_shearing_sessions_start_date"), "shearing_sessions", ["start_date"], unique=False)
    op.create_index(op.f("ix_shearing_sessions_status"), "shearing_sessions", ["status"], unique=False)

    op.create_table(
        "shearing_bale_codes",
        sa.Column("species", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("code_key", sa.String(length=60), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("line_type", sa.String(length=80), nullable=True),
        sa.Column("age_group", sa.String(length=80), nullable=True),
        sa.Column("fineness_grade", sa.String(length=80), nullable=True),
        sa.Column("length_code", sa.String(length=20), nullable=True),
        sa.Column("fineness_micron", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("clean_yield_percent", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("style_character", sa.String(length=120), nullable=True),
        sa.Column("consistency", sa.String(length=120), nullable=True),
        sa.Column("color", sa.String(length=80), nullable=True),
        sa.Column("vegetable_matter", sa.String(length=80), nullable=True),
        sa.Column("fault", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("species IN ('Sheep', 'Goat')", name="ck_shearing_bale_code_species_allowed"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("species", "code_key", name="uq_shearing_bale_code_species_code_key"),
    )
    op.create_index(op.f("ix_shearing_bale_codes_active"), "shearing_bale_codes", ["active"], unique=False)
    op.create_index(op.f("ix_shearing_bale_codes_species"), "shearing_bale_codes", ["species"], unique=False)

    op.create_table(
        "shearing_entries",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("shearer_id", sa.String(length=36), nullable=False),
        sa.Column("animal_group_type_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_shearing_entry_quantity_positive"),
        sa.ForeignKeyConstraint(["animal_group_type_id"], ["animal_group_types.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["shearing_sessions.id"]),
        sa.ForeignKeyConstraint(["shearer_id"], ["shearers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "work_date",
            "shearer_id",
            "animal_group_type_id",
            name="uq_shearing_entry_daily_shearer_group",
        ),
    )
    op.create_index(op.f("ix_shearing_entries_animal_group_type_id"), "shearing_entries", ["animal_group_type_id"], unique=False)
    op.create_index(op.f("ix_shearing_entries_shearer_id"), "shearing_entries", ["shearer_id"], unique=False)
    op.create_index(op.f("ix_shearing_entries_session_id"), "shearing_entries", ["session_id"], unique=False)
    op.create_index(op.f("ix_shearing_entries_work_date"), "shearing_entries", ["work_date"], unique=False)

    op.create_table(
        "shearing_bales",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("bale_code_id", sa.String(length=36), nullable=True),
        sa.Column("code_text", sa.String(length=60), nullable=False),
        sa.Column("code_key", sa.String(length=60), nullable=False),
        sa.Column("bale_number", sa.String(length=60), nullable=True),
        sa.Column("weight_kg", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("price_per_kg", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("total_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("pricing_input_mode", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("weight_kg > 0", name="ck_shearing_bale_weight_positive"),
        sa.CheckConstraint(
            "price_per_kg IS NULL OR price_per_kg >= 0",
            name="ck_shearing_bale_price_per_kg_non_negative",
        ),
        sa.CheckConstraint(
            "total_price IS NULL OR total_price >= 0",
            name="ck_shearing_bale_total_price_non_negative",
        ),
        sa.CheckConstraint(
            "pricing_input_mode IN ('unpriced', 'price_per_kg', 'total_price', 'both')",
            name="ck_shearing_bale_pricing_input_mode",
        ),
        sa.ForeignKeyConstraint(["bale_code_id"], ["shearing_bale_codes.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["shearing_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shearing_bales_bale_code_id"), "shearing_bales", ["bale_code_id"], unique=False)
    op.create_index(op.f("ix_shearing_bales_code_key"), "shearing_bales", ["code_key"], unique=False)
    op.create_index(op.f("ix_shearing_bales_session_id"), "shearing_bales", ["session_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_shearing_bales_session_id"), table_name="shearing_bales")
    op.drop_index(op.f("ix_shearing_bales_code_key"), table_name="shearing_bales")
    op.drop_index(op.f("ix_shearing_bales_bale_code_id"), table_name="shearing_bales")
    op.drop_table("shearing_bales")
    op.drop_index(op.f("ix_shearing_entries_work_date"), table_name="shearing_entries")
    op.drop_index(op.f("ix_shearing_entries_session_id"), table_name="shearing_entries")
    op.drop_index(op.f("ix_shearing_entries_shearer_id"), table_name="shearing_entries")
    op.drop_index(op.f("ix_shearing_entries_animal_group_type_id"), table_name="shearing_entries")
    op.drop_table("shearing_entries")
    op.drop_index(op.f("ix_shearing_bale_codes_species"), table_name="shearing_bale_codes")
    op.drop_index(op.f("ix_shearing_bale_codes_active"), table_name="shearing_bale_codes")
    op.drop_table("shearing_bale_codes")
    op.drop_index(op.f("ix_shearing_sessions_status"), table_name="shearing_sessions")
    op.drop_index(op.f("ix_shearing_sessions_start_date"), table_name="shearing_sessions")
    op.drop_index(op.f("ix_shearing_sessions_farm_id"), table_name="shearing_sessions")
    op.drop_index(op.f("ix_shearing_sessions_end_date"), table_name="shearing_sessions")
    op.drop_table("shearing_sessions")
    op.drop_table("shearers")
