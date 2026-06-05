"""Add water asset notes and note attachments.

Revision ID: c5d8e7f2a901
Revises: ad3f9b2c6e10
Create Date: 2026-06-02 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c5d8e7f2a901"
down_revision = "ad3f9b2c6e10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "water_asset_events",
        sa.Column("water_asset_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column(
            "event_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["water_asset_id"], ["water_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_water_asset_events_event_at"), "water_asset_events", ["event_at"], unique=False)
    op.create_index(op.f("ix_water_asset_events_farm_id"), "water_asset_events", ["farm_id"], unique=False)
    op.create_index(
        op.f("ix_water_asset_events_water_asset_id"),
        "water_asset_events",
        ["water_asset_id"],
        unique=False,
    )

    op.create_table(
        "note_attachments",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("mob_event_id", sa.String(length=36), nullable=True),
        sa.Column("paddock_event_id", sa.String(length=36), nullable=True),
        sa.Column("water_asset_event_id", sa.String(length=36), nullable=True),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("client_attachment_id", sa.String(length=120), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_note_attachments_byte_size_non_negative"),
        sa.CheckConstraint(
            (
                "(mob_event_id IS NOT NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL) "
                "OR (mob_event_id IS NULL AND paddock_event_id IS NOT NULL AND water_asset_event_id IS NULL) "
                "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NOT NULL)"
            ),
            name="ck_note_attachments_one_event",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["mob_event_id"], ["mob_events.id"]),
        sa.ForeignKeyConstraint(["paddock_event_id"], ["paddock_events.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["water_asset_event_id"], ["water_asset_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "uploaded_by_user_id",
            "client_attachment_id",
            name="uq_note_attachments_user_client_attachment",
        ),
    )
    op.create_index(op.f("ix_note_attachments_farm_id"), "note_attachments", ["farm_id"], unique=False)
    op.create_index(
        op.f("ix_note_attachments_mob_event_id"),
        "note_attachments",
        ["mob_event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_note_attachments_paddock_event_id"),
        "note_attachments",
        ["paddock_event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_note_attachments_uploaded_by_user_id"),
        "note_attachments",
        ["uploaded_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_note_attachments_water_asset_event_id"),
        "note_attachments",
        ["water_asset_event_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_note_attachments_water_asset_event_id"), table_name="note_attachments")
    op.drop_index(op.f("ix_note_attachments_uploaded_by_user_id"), table_name="note_attachments")
    op.drop_index(op.f("ix_note_attachments_paddock_event_id"), table_name="note_attachments")
    op.drop_index(op.f("ix_note_attachments_mob_event_id"), table_name="note_attachments")
    op.drop_index(op.f("ix_note_attachments_farm_id"), table_name="note_attachments")
    op.drop_table("note_attachments")
    op.drop_index(op.f("ix_water_asset_events_water_asset_id"), table_name="water_asset_events")
    op.drop_index(op.f("ix_water_asset_events_farm_id"), table_name="water_asset_events")
    op.drop_index(op.f("ix_water_asset_events_event_at"), table_name="water_asset_events")
    op.drop_table("water_asset_events")
