"""Add stored reference images to livestock trades.

Revision ID: c6d8e0f213a5
Revises: b4c6d8e0f213
"""

import sqlalchemy as sa
from alembic import op


revision = "c6d8e0f213a5"
down_revision = "b4c6d8e0f213"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "livestock_trade_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("livestock_trade_id", sa.String(36), sa.ForeignKey("livestock_trades.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_livestock_trade_attachment_size_positive"),
        sa.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_attachment_order"),
        sa.UniqueConstraint("livestock_trade_id", "sort_order", name="uq_livestock_trade_attachment_order"),
    )
    op.create_index("ix_livestock_trade_attachments_livestock_trade_id", "livestock_trade_attachments", ["livestock_trade_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM livestock_trade_attachments")).scalar():
        raise RuntimeError("Cannot downgrade without losing livestock reference images. Restore the pre-upgrade backup instead.")
    op.drop_index("ix_livestock_trade_attachments_livestock_trade_id", table_name="livestock_trade_attachments")
    op.drop_table("livestock_trade_attachments")
