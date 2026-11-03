"""Use calculated storage as the single capacity for polygon weirs.

Revision ID: b8d4f0a2c613
Revises: a7c3e9f1b502

Capacity is a derived current value, not a historical observation. Use the
installed calculation model, just as API reads do, without touching timestamps
or level history. Point-only weirs and other manually sized assets are unchanged.
"""

import logging
from decimal import Decimal
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import context, op

revision = "b8d4f0a2c613"
down_revision = "a7c3e9f1b502"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Polygon capacity backfill requires an online database connection")
    # This is a materialization of current derived geometry, so it deliberately
    # uses the current calculator, not a second frozen copy of the model.
    from app.services.weir_volume_service import estimate

    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")
    assets = sa.table(
        "water_assets",
        sa.column("id", sa.String),
        sa.column("asset_type", sa.String),
        sa.column("capacity_m3", sa.Numeric(12, 2)),
        sa.column("weir_polygon", sa.JSON),
        sa.column("weir_centerline", sa.JSON),
        sa.column("weir_wall_height_m", sa.Numeric(8, 3)),
        sa.column("weir_height_assumed", sa.Boolean),
        sa.column("weir_geometry_review_required", sa.Boolean),
        sa.column("water_level_percent", sa.Numeric(5, 2)),
    )
    rows = list(bind.execute(sa.select(assets).where(assets.c.asset_type == "weir")).mappings())
    updated = 0
    for row in rows:
        if not row["weir_polygon"]:
            continue
        capacity = estimate(SimpleNamespace(**row))["capacity_m3"]
        bind.execute(
            assets.update().where(assets.c.id == row["id"]).values(
                capacity_m3=Decimal(str(capacity)) if capacity is not None else None
            )
        )
        updated += 1
    logging.getLogger("alembic.runtime.migration").info("Updated capacity for %d polygon weirs", updated)


def downgrade():
    raise RuntimeError("Previous manual polygon-weir capacities cannot be reconstructed. Restore the pre-migration backup.")
