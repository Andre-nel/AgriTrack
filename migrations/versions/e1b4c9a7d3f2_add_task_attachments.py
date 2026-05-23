"""Add task attachments

Revision ID: e1b4c9a7d3f2
Revises: bd91c3e7a402
Create Date: 2026-05-23 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e1b4c9a7d3f2"
down_revision = "bd91c3e7a402"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_attachments",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=False),
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
        sa.CheckConstraint("byte_size >= 0", name="ck_task_attachments_byte_size_non_negative"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "uploaded_by_user_id",
            "client_attachment_id",
            name="uq_task_attachments_user_client_attachment",
        ),
    )
    op.create_index(op.f("ix_task_attachments_task_id"), "task_attachments", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_task_attachments_uploaded_by_user_id"),
        "task_attachments",
        ["uploaded_by_user_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f("ix_task_attachments_uploaded_by_user_id"), table_name="task_attachments")
    op.drop_index(op.f("ix_task_attachments_task_id"), table_name="task_attachments")
    op.drop_table("task_attachments")
