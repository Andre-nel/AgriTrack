"""Add shearing session attachments.

Revision ID: 5a1c9e7d2b44
Revises: 4f2b9d8c6a11
Create Date: 2026-07-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "5a1c9e7d2b44"
down_revision = "4f2b9d8c6a11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shearing_session_attachments",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_shearing_session_attachments_byte_size_non_negative",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["shearing_sessions.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_shearing_session_attachments_session_id"),
        "shearing_session_attachments",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shearing_session_attachments_uploaded_by_user_id"),
        "shearing_session_attachments",
        ["uploaded_by_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_shearing_session_attachments_uploaded_by_user_id"),
        table_name="shearing_session_attachments",
    )
    op.drop_index(
        op.f("ix_shearing_session_attachments_session_id"),
        table_name="shearing_session_attachments",
    )
    op.drop_table("shearing_session_attachments")
