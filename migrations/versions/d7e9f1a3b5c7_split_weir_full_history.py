"""Estimate Overflowing for the first half of historical weir Full periods.

Revision ID: d7e9f1a3b5c7
Revises: c6d8e0f213a5

This is a user-requested retrospective estimate, not an observed reading.
Restore a pre-migration backup to undo it; a downgrade cannot distinguish
converted readings from subsequently observed Overflowing readings.
"""

import logging
import uuid
from datetime import datetime, timezone

from alembic import context, op
import sqlalchemy as sa


revision = "d7e9f1a3b5c7"
down_revision = "c6d8e0f213a5"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")
STATE_FIELDS = ("active", "status", "water_level")


def _utc_naive(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_history(asset, rows, cutoff):
    for index, row in enumerate(rows):
        if row["changed_at"] > cutoff:
            raise RuntimeError(f"Weir {asset['id']} has future history; conversion aborted")
        if index and any(
            row[f"previous_{field}"] != rows[index - 1][field] for field in STATE_FIELDS
        ):
            raise RuntimeError(f"Weir {asset['id']} has inconsistent history; conversion aborted")
    if rows and any(rows[-1][field] != asset[field] for field in STATE_FIELDS):
        raise RuntimeError(f"Weir {asset['id']} current state differs from history; conversion aborted")


def _split_full_periods(rows, cutoff):
    """Return a copied timeline, plus converted/skipped period counts.

    Entries at an identical timestamp retain the existing ID ordering. Reusing
    midpoint entries avoids introducing an arbitrary UUID ordering at a tie.
    """
    result = [dict(row) for row in rows]
    converted = skipped = 0
    index = 0
    while index < len(rows):
        if rows[index]["water_level"] != "full":
            index += 1
            continue
        start_index = index
        while index < len(rows) and rows[index]["water_level"] == "full":
            index += 1
        start = rows[start_index]["changed_at"]
        end = rows[index]["changed_at"] if index < len(rows) else cutoff
        # Python and the supported databases retain microseconds. Floor an odd
        # microsecond into the remaining Full half instead of crossing an endpoint.
        midpoint = start + (end - start) // 2
        if not start < midpoint < end:
            skipped += 1
            continue
        converted += 1
        for position in range(start_index, index):
            if rows[position]["changed_at"] < midpoint:
                result[position]["water_level"] = "overflowing"
        if not any(rows[position]["changed_at"] == midpoint for position in range(start_index, index)):
            previous = next(
                rows[position]
                for position in range(index - 1, start_index - 1, -1)
                if rows[position]["changed_at"] < midpoint
            )
            result.append({
                "id": str(uuid.uuid4()),
                "water_asset_id": previous["water_asset_id"],
                "farm_id": previous["farm_id"],
                "change_type": "updated",
                "changed_at": midpoint,
                "previous_active": previous["active"],
                "previous_status": previous["status"],
                "previous_water_level": "overflowing",
                "active": previous["active"],
                "status": previous["status"],
                "water_level": "full",
            })
    result.sort(key=lambda row: (row["changed_at"], row["id"]))
    for previous, current in zip(result, result[1:]):
        current["previous_water_level"] = previous["water_level"]
    return result, converted, skipped


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Weir history conversion requires an online database connection")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        # sqlite3's legacy mode does not BEGIN for SELECT. Lock before reading so
        # history, changes and the Alembic revision commit in one transaction.
        bind.exec_driver_sql("BEGIN IMMEDIATE")
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    assets = sa.table(
        "water_assets",
        sa.column("id", sa.String(36)),
        sa.column("farm_id", sa.String(36)),
        sa.column("asset_type", sa.String(30)),
        sa.column("active", sa.Boolean()),
        sa.column("status", sa.String(40)),
        sa.column("water_level", sa.String(30)),
    )
    history = sa.table(
        "water_asset_state_history",
        sa.column("id", sa.String(36)),
        sa.column("water_asset_id", sa.String(36)),
        sa.column("farm_id", sa.String(36)),
        sa.column("change_type", sa.String(20)),
        sa.column("changed_at", sa.DateTime(timezone=True)),
        sa.column("previous_active", sa.Boolean()),
        sa.column("previous_status", sa.String(40)),
        sa.column("previous_water_level", sa.String(30)),
        sa.column("active", sa.Boolean()),
        sa.column("status", sa.String(40)),
        sa.column("water_level", sa.String(30)),
    )
    plans = []
    converted_count = skipped_count = missing_count = 0
    # Validate every asset before the first write, so inconsistent source data
    # cannot leave a partially interpreted history even on a failed migration.
    for asset in bind.execute(sa.select(assets).where(assets.c.asset_type == "weir")).mappings():
        rows = [dict(row) for row in bind.execute(
            sa.select(history).where(history.c.water_asset_id == asset["id"])
            .order_by(history.c.changed_at, history.c.id)
        ).mappings()]
        for row in rows:
            row["changed_at"] = _utc_naive(row["changed_at"])
            if row["farm_id"] != asset["farm_id"]:
                raise RuntimeError(f"Weir {asset['id']} has history in another farm; conversion aborted")
        _validate_history(asset, rows, cutoff)
        if not rows:
            missing_count += 1
            continue
        revised, converted, skipped = _split_full_periods(rows, cutoff)
        plans.append(({row["id"]: row for row in rows}, revised))
        converted_count += converted
        skipped_count += skipped

    inserted_count = updated_count = 0
    for originals, revised in plans:
        for row in revised:
            original = originals.get(row["id"])
            if original is None:
                bind.execute(history.insert().values(**row))
                inserted_count += 1
            else:
                changes = {
                    field: row[field] for field in ("water_level", "previous_water_level")
                    if row[field] != original[field]
                }
                if changes:
                    bind.execute(history.update().where(history.c.id == row["id"]).values(**changes))
                    updated_count += 1
    logger.info(
        "Weir Full conversion cutoff=%s UTC: %d periods, %d inserted rows, "
        "%d updated rows, %d short periods skipped, %d assets without history",
        cutoff.isoformat(), converted_count, inserted_count, updated_count,
        skipped_count, missing_count,
    )


def downgrade():
    raise RuntimeError(
        "Weir history conversion cannot be downgraded. Restore the pre-migration "
        "backup to recover the original observations."
    )
