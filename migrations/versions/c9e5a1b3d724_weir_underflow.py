"""Record weir underflow alongside measured depth in assets and history."""

import sqlalchemy as sa
from alembic import op

revision = "c9e5a1b3d724"
down_revision = "b8d4f0a2c613"
branch_labels = None
depends_on = None


def _begin():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _begin()
    for table, name, condition in (
        ("water_assets", "ck_water_asset_underflow", "asset_type = 'weir' AND NOT is_overflowing"),
        ("water_asset_state_history", "ck_water_history_underflow", "NOT is_overflowing"),
    ):
        op.add_column(table, sa.Column("is_underflowing", sa.Boolean(), sa.CheckConstraint(
            f"NOT is_underflowing OR ({condition} AND status IS NOT NULL AND status = 'damaged')",
            name=name,
        ), nullable=False, server_default=sa.false()))
    op.add_column("water_asset_state_history", sa.Column("previous_is_underflowing", sa.Boolean(), sa.CheckConstraint(
        "NOT previous_is_underflowing OR (previous_is_overflowing IS FALSE AND previous_status IS NOT NULL AND previous_status = 'damaged')",
        name="ck_water_history_previous_underflow",
    )))
    op.execute("UPDATE water_asset_state_history SET previous_is_underflowing = FALSE WHERE previous_is_overflowing IS NOT NULL")


def downgrade():
    _begin()
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM water_assets WHERE is_underflowing UNION ALL "
        "SELECT 1 FROM water_asset_state_history WHERE is_underflowing OR previous_is_underflowing LIMIT 1"
    )).first():
        raise RuntimeError("Downgrade would lose recorded underflow. Restore the pre-upgrade backup.")
    op.drop_column("water_asset_state_history", "previous_is_underflowing")
    op.drop_column("water_asset_state_history", "is_underflowing")
    op.drop_column("water_assets", "is_underflowing")
