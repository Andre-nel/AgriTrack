"""Record farm capacity and inventory history from an observed baseline.

Revision ID: fa2b4c6d8e10
Revises: 8f9a0b1c2d3e
"""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from alembic import op
import sqlalchemy as sa


revision = "fa2b4c6d8e10"
down_revision = "8f9a0b1c2d3e"
branch_labels = None
depends_on = None


def upgrade():
    capacity = op.create_table(
        "farm_capacity_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("farm_id", sa.String(36), sa.ForeignKey("farms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capacity_lsu", sa.Numeric(18, 6), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.CheckConstraint("source IN ('baseline', 'created', 'change')", name="ck_farm_capacity_history_source"),
    )
    for column in ("farm_id", "effective_at"):
        op.create_index(f"ix_farm_capacity_history_{column}", "farm_capacity_history", [column])

    state = op.create_table(
        "mob_inventory_state_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("mob_id", sa.String(36), sa.ForeignKey("mobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("farm_id", sa.String(36), sa.ForeignKey("farms.id", ondelete="SET NULL")),
        sa.Column("previous_farm_id", sa.String(36), sa.ForeignKey("farms.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("previous_active", sa.Boolean()),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.CheckConstraint("source IN ('baseline', 'created', 'move', 'status')", name="ck_inventory_state_source"),
        sqlite_autoincrement=True,
    )
    for column in ("mob_id", "farm_id", "effective_at"):
        op.create_index(f"ix_mob_inventory_state_history_{column}", "mob_inventory_state_history", [column])
    op.create_index(
        "uq_inventory_initial_state", "mob_inventory_state_history", ["mob_id"], unique=True,
        sqlite_where=sa.text("source IN ('baseline', 'created')"),
        postgresql_where=sa.text("source IN ('baseline', 'created')"),
    )
    groups = op.create_table(
        "mob_inventory_baseline_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state_id", sa.Integer(), sa.ForeignKey("mob_inventory_state_history.id", ondelete="CASCADE"), nullable=False),
        sa.Column("animal_group_type_id", sa.String(36), sa.ForeignKey("animal_group_types.id"), nullable=False),
        sa.Column("head_count", sa.Integer(), nullable=False),
        sa.Column("ledger_net", sa.Integer(), nullable=False),
        sa.CheckConstraint("head_count >= 0", name="ck_inventory_baseline_head_count"),
        sa.UniqueConstraint("state_id", "animal_group_type_id", name="uq_inventory_baseline_group"),
    )
    op.create_index("ix_mob_inventory_baseline_groups_state_id", "mob_inventory_baseline_groups", ["state_id"])

    # A common observation time is intentional: created_at is not evidence of
    # historical capacity or opening inventory. Never import live service code
    # into migrations, whose calculation must remain stable in future releases.
    captured_at = datetime.now(timezone.utc)
    bind = op.get_bind()
    farm_table = sa.table(
        "farms", sa.column("id", sa.String(36)),
        sa.column("default_stocking_rate_ha_per_lsu", sa.Numeric(8, 2)),
    )
    paddock_table = sa.table(
        "paddocks", sa.column("farm_id", sa.String(36)),
        sa.column("area_ha", sa.Numeric(10, 2)),
        sa.column("grazeable_area_ha", sa.Numeric(10, 2)),
        sa.column("stocking_rate_ha_per_lsu_override", sa.Numeric(8, 2)),
        sa.column("status", sa.String(20)),
    )
    # SQLite can physically retain more decimal places than its declared scale;
    # typed results match the values that the application actually calculates.
    farms = bind.execute(sa.select(farm_table)).mappings().all()
    paddocks = bind.execute(sa.select(paddock_table).where(paddock_table.c.status == "active")).mappings().all()
    by_farm = defaultdict(list)
    for paddock in paddocks:
        by_farm[paddock["farm_id"]].append(paddock)
    for farm in farms:
        total = Decimal(0)
        for paddock in by_farm[farm["id"]]:
            grazeable = Decimal(str(paddock["grazeable_area_ha"] or 0))
            area = grazeable if grazeable > 0 else Decimal(str(paddock["area_ha"] or 0))
            rate = Decimal(str(paddock["stocking_rate_ha_per_lsu_override"] or farm["default_stocking_rate_ha_per_lsu"]))
            total += area / rate
        bind.execute(capacity.insert().values(
            farm_id=farm["id"], effective_at=captured_at, recorded_at=captured_at,
            capacity_lsu=total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), source="baseline",
        ))

    counts = defaultdict(dict)
    for row in bind.execute(sa.text(
        "SELECT mob_id, animal_group_type_id, SUM(head_count) AS heads "
        "FROM animal_group_balances GROUP BY mob_id, animal_group_type_id"
    )).mappings():
        counts[row["mob_id"]][row["animal_group_type_id"]] = int(row["heads"])
    ledger = defaultdict(dict)
    for row in bind.execute(sa.text(
        "SELECT mob_id, animal_group_type_id, SUM(CASE "
        "WHEN event_type IN ('birth','purchase','transfer_in','adjustment_in') THEN quantity "
        "WHEN event_type IN ('death','sale','missing','transfer_out','adjustment_out') THEN -quantity "
        "ELSE 0 END) AS net FROM stock_ledger_entries GROUP BY mob_id, animal_group_type_id"
    )).mappings():
        ledger[row["mob_id"]][row["animal_group_type_id"]] = int(row["net"])
    for mob in bind.execute(sa.text("SELECT id, farm_id, status FROM mobs")).mappings().all():
        state_id = bind.execute(state.insert().values(
            mob_id=mob["id"], farm_id=mob["farm_id"], active=mob["status"] == "active",
            effective_at=captured_at, recorded_at=captured_at, source="baseline",
        )).inserted_primary_key[0]
        mob_counts, mob_ledger = counts[mob["id"]], ledger[mob["id"]]
        for group_id in sorted(mob_counts.keys() | mob_ledger.keys()):
            bind.execute(groups.insert().values(
                state_id=state_id, animal_group_type_id=group_id,
                head_count=mob_counts.get(group_id, 0), ledger_net=mob_ledger.get(group_id, 0),
            ))


def downgrade():
    op.drop_table("mob_inventory_baseline_groups")
    op.drop_table("mob_inventory_state_history")
    op.drop_table("farm_capacity_history")
