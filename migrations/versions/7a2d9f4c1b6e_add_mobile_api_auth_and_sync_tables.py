"""Add mobile API auth and sync tables.

Revision ID: 7a2d9f4c1b6e
Revises: 5e9b1c2d7f4a
Create Date: 2026-05-18 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a2d9f4c1b6e"
down_revision = "5e9b1c2d7f4a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))

    op.create_table(
        "mobile_auth_tokens",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=12), nullable=False),
        sa.Column("device_name", sa.String(length=120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_mobile_auth_tokens_expires_at"), "mobile_auth_tokens", ["expires_at"], unique=False)
    op.create_index(op.f("ix_mobile_auth_tokens_revoked_at"), "mobile_auth_tokens", ["revoked_at"], unique=False)
    op.create_index(op.f("ix_mobile_auth_tokens_token_hash"), "mobile_auth_tokens", ["token_hash"], unique=True)
    op.create_index(op.f("ix_mobile_auth_tokens_token_prefix"), "mobile_auth_tokens", ["token_prefix"], unique=False)
    op.create_index(op.f("ix_mobile_auth_tokens_user_id"), "mobile_auth_tokens", ["user_id"], unique=False)

    op.create_table(
        "mobile_sync_commands",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("client_command_id", sa.String(length=120), nullable=False),
        sa.Column("command_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("status IN ('applied', 'failed')", name="ck_mobile_sync_command_status"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_command_id", name="uq_mobile_sync_user_command"),
    )
    op.create_index(op.f("ix_mobile_sync_commands_command_type"), "mobile_sync_commands", ["command_type"], unique=False)
    op.create_index(op.f("ix_mobile_sync_commands_farm_id"), "mobile_sync_commands", ["farm_id"], unique=False)
    op.create_index(op.f("ix_mobile_sync_commands_processed_at"), "mobile_sync_commands", ["processed_at"], unique=False)
    op.create_index(op.f("ix_mobile_sync_commands_status"), "mobile_sync_commands", ["status"], unique=False)
    op.create_index(op.f("ix_mobile_sync_commands_user_id"), "mobile_sync_commands", ["user_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_mobile_sync_commands_user_id"), table_name="mobile_sync_commands")
    op.drop_index(op.f("ix_mobile_sync_commands_status"), table_name="mobile_sync_commands")
    op.drop_index(op.f("ix_mobile_sync_commands_processed_at"), table_name="mobile_sync_commands")
    op.drop_index(op.f("ix_mobile_sync_commands_farm_id"), table_name="mobile_sync_commands")
    op.drop_index(op.f("ix_mobile_sync_commands_command_type"), table_name="mobile_sync_commands")
    op.drop_table("mobile_sync_commands")

    op.drop_index(op.f("ix_mobile_auth_tokens_user_id"), table_name="mobile_auth_tokens")
    op.drop_index(op.f("ix_mobile_auth_tokens_token_prefix"), table_name="mobile_auth_tokens")
    op.drop_index(op.f("ix_mobile_auth_tokens_token_hash"), table_name="mobile_auth_tokens")
    op.drop_index(op.f("ix_mobile_auth_tokens_revoked_at"), table_name="mobile_auth_tokens")
    op.drop_index(op.f("ix_mobile_auth_tokens_expires_at"), table_name="mobile_auth_tokens")
    op.drop_table("mobile_auth_tokens")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("password_hash")
