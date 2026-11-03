"""Polygon weirs and canonical depth percentages.

Revision ID: a7c3e9f1b502
Revises: f9b2d4e6a810

Historical labels are retrospective estimates, not new observations. Keep this
mapping frozen here: later application changes must not rewrite this migration.
"""

import logging

import sqlalchemy as sa
from alembic import context, op

revision = "a7c3e9f1b502"
down_revision = "f9b2d4e6a810"
branch_labels = None
depends_on = None
LEVELS = {"empty": 0, "low": 25, "half": 50, "high": 75, "full": 100, "overflowing": 100}


def _converted(value):
    if value is None or value in ("", "unknown"):
        return None, False
    if value not in LEVELS:
        raise RuntimeError(f"Unexpected historical weir level {value!r}; migration aborted")
    return LEVELS[value], value == "overflowing"


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Weir percentage conversion requires an online database connection")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")
    assets = list(bind.execute(sa.text("SELECT id, water_level FROM water_assets WHERE asset_type = 'weir'")).mappings())
    history = list(bind.execute(sa.text("SELECT h.id, h.water_level, h.previous_water_level FROM water_asset_state_history h JOIN water_assets a ON a.id = h.water_asset_id WHERE a.asset_type = 'weir'")).mappings())
    # Preflight everything before touching schema or observations.
    for row in assets:
        _converted(row["water_level"])
    for row in history:
        _converted(row["water_level"])
        _converted(row["previous_water_level"])

    for table in ("water_assets", "water_asset_state_history"):
        op.add_column(table, sa.Column("water_level_percent", sa.Numeric(5, 2)))
        op.add_column(table, sa.Column("is_overflowing", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("water_asset_state_history", sa.Column("previous_water_level_percent", sa.Numeric(5, 2)))
    op.add_column("water_asset_state_history", sa.Column("previous_is_overflowing", sa.Boolean()))
    for column in (
        sa.Column("weir_polygon", sa.JSON()),
        sa.Column("weir_centerline", sa.JSON()),
        sa.Column("weir_wall_height_m", sa.Numeric(8, 3)),
        sa.Column("weir_height_assumed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("weir_geometry_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("water_assets", column)
    for row in assets:
        percent, overflow = _converted(row["water_level"])
        bind.execute(sa.text("UPDATE water_assets SET water_level = NULL, water_level_percent = :percent, is_overflowing = :overflow WHERE id = :id"), dict(id=row["id"], percent=percent, overflow=overflow))
    for row in history:
        percent, overflow = _converted(row["water_level"])
        previous, previous_overflow = _converted(row["previous_water_level"])
        bind.execute(sa.text("UPDATE water_asset_state_history SET water_level = NULL, previous_water_level = NULL, water_level_percent = :percent, is_overflowing = :overflow, previous_water_level_percent = :previous, previous_is_overflowing = :previous_overflow WHERE id = :id"),
                     dict(id=row["id"], percent=percent, overflow=overflow, previous=previous, previous_overflow=previous_overflow))
    if bind.dialect.name == "sqlite":
        # Rebuilding the parent table temporarily removes referenced IDs. Keep
        # FK enforcement enabled, defer it during the rebuild, then verify the
        # completed schema before resetting SQLite's stale DROP TABLE counters.
        bind.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    with op.batch_alter_table("water_assets") as batch:
        batch.create_check_constraint("ck_water_asset_percent", "water_level_percent IS NULL OR (water_level_percent >= 0 AND water_level_percent <= 100)")
        batch.create_check_constraint("ck_water_asset_overflow_percent", "NOT is_overflowing OR (water_level_percent IS NOT NULL AND water_level_percent = 100)")
        batch.create_check_constraint("ck_water_asset_wall_height", "weir_wall_height_m IS NULL OR weir_wall_height_m > 0")
        batch.create_check_constraint("ck_water_asset_percent_type", "asset_type = 'weir' OR (water_level_percent IS NULL AND NOT is_overflowing)")
        batch.create_check_constraint("ck_water_asset_weir_canonical_level", "asset_type <> 'weir' OR water_level IS NULL")
    with op.batch_alter_table("water_asset_state_history") as batch:
        batch.create_check_constraint("ck_water_history_percent", "water_level_percent IS NULL OR (water_level_percent >= 0 AND water_level_percent <= 100)")
        batch.create_check_constraint("ck_water_history_previous_percent", "previous_water_level_percent IS NULL OR (previous_water_level_percent >= 0 AND previous_water_level_percent <= 100)")
        batch.create_check_constraint("ck_water_history_overflow_percent", "NOT is_overflowing OR (water_level_percent IS NOT NULL AND water_level_percent = 100)")
        batch.create_check_constraint("ck_water_history_previous_overflow_percent", "previous_is_overflowing IS NULL OR NOT previous_is_overflowing OR (previous_water_level_percent IS NOT NULL AND previous_water_level_percent = 100)")
    if bind.dialect.name == "sqlite":
        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key integrity failed after weir migration: {violations[:5]}")
        bind.exec_driver_sql("PRAGMA defer_foreign_keys=OFF")
    logging.getLogger("alembic.runtime.migration").info("Converted %d weirs and %d history rows to depth percentages; timestamps and IDs preserved", len(assets), len(history))


def downgrade():
    raise RuntimeError("Percentage readings and polygon setup cannot be downgraded without data loss. Restore the pre-migration backup.")
