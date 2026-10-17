"""Move livestock pricing and weights into groups without reposting cashflow.

Revision ID: b4c6d8e0f213
Revises: fa2b4c6d8e10
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "b4c6d8e0f213"
down_revision = "fa2b4c6d8e10"
branch_labels = None
depends_on = None


def _identity():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _pricing(count_name):
    return [
        sa.Column("price_per_kg", sa.Numeric(12, 4)),
        sa.Column("price_per_head", sa.Numeric(12, 2)),
        sa.Column(count_name, sa.Integer()),
        sa.Column("hair_lengths", sa.String(250)),
    ]


def _create_tables(grouped):
    columns = [
        *_identity(), sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("trade_type", sa.String(20), nullable=False),
        sa.Column("counterparty", sa.String(120), nullable=False),
        sa.Column("reference", sa.String(120)), sa.Column("vat_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("pricing_model", sa.String(20), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("cash_transaction_id", sa.String(36), sa.ForeignKey("cash_transactions.id"), nullable=False),
        sa.CheckConstraint("trade_type IN ('sale', 'purchase')", name="ck_livestock_trade_type_allowed"),
        sa.CheckConstraint("pricing_model IN ('weight', 'head')", name="ck_livestock_trade_pricing_model_allowed"),
    ]
    if grouped:
        columns.extend([
            sa.Column("species", sa.String(20), nullable=False),
            sa.Column("import_id", sa.String(36)), sa.Column("import_payload_hash", sa.String(64)),
            sa.UniqueConstraint("import_id", name="uq_livestock_trade_import_id"),
            sa.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="ck_livestock_trade_vat_range"),
            sa.CheckConstraint("species IN ('Cattle', 'Sheep', 'Goat')", name="ck_livestock_trade_species"),
        ])
    else:
        columns.extend([
            sa.Column("animal_group_type_id", sa.String(36), sa.ForeignKey("animal_group_types.id"), nullable=False),
            *_pricing("goat_count"),
            sa.CheckConstraint("vat_rate >= 0", name="ck_livestock_trade_vat_non_negative"),
            sa.CheckConstraint("price_per_kg IS NULL OR price_per_kg > 0", name="ck_livestock_trade_price_per_kg_positive"),
            sa.CheckConstraint("price_per_head IS NULL OR price_per_head > 0", name="ck_livestock_trade_price_per_head_positive"),
            sa.CheckConstraint("goat_count IS NULL OR goat_count > 0", name="ck_livestock_trade_goat_count_positive"),
        ])
    trade = op.create_table("livestock_trades", *columns)
    for column in ("trade_date", "trade_type", "cash_transaction_id", "species" if grouped else "animal_group_type_id"):
        op.create_index(f"ix_livestock_trades_{column}", "livestock_trades", [column], unique=column == "cash_transaction_id")
    group = None
    if grouped:
        group = op.create_table(
            "livestock_trade_groups", *_identity(),
            sa.Column("livestock_trade_id", sa.String(36), sa.ForeignKey("livestock_trades.id"), nullable=False),
            sa.Column("animal_group_type_id", sa.String(36), sa.ForeignKey("animal_group_types.id"), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(120)), sa.Column("notes", sa.Text()), *_pricing("head_count"),
            sa.UniqueConstraint("livestock_trade_id", "sort_order", name="uq_livestock_trade_group_order"),
            sa.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_group_order"),
            sa.CheckConstraint(
                "(price_per_kg IS NOT NULL AND price_per_kg > 0 AND price_per_head IS NULL AND head_count IS NULL) OR "
                "(price_per_kg IS NULL AND price_per_head IS NOT NULL AND price_per_head > 0 AND head_count IS NOT NULL AND head_count > 0)",
                name="ck_livestock_trade_group_pricing",
            ),
        )
        for column in ("livestock_trade_id", "animal_group_type_id"):
            op.create_index(f"ix_livestock_trade_groups_{column}", "livestock_trade_groups", [column])
    farm = op.create_table(
        "livestock_trade_farms", *_identity(),
        sa.Column("livestock_trade_id", sa.String(36), sa.ForeignKey("livestock_trades.id"), nullable=False),
        sa.Column("farm_id", sa.String(36), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("livestock_trade_id", "farm_id", name="uq_livestock_trade_farm"),
        sa.CheckConstraint("sort_order >= 0", name="ck_livestock_trade_farm_sort_non_negative"),
    )
    for column in ("livestock_trade_id", "farm_id"):
        op.create_index(f"ix_livestock_trade_farms_{column}", "livestock_trade_farms", [column])
    owner = "group_id" if grouped else "livestock_trade_id"
    weight = op.create_table(
        "livestock_trade_weights", *_identity(),
        sa.Column(owner, sa.String(36), sa.ForeignKey("livestock_trade_groups.id" if grouped else "livestock_trades.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("weight_kg", sa.Numeric(10, 3), nullable=False),
        sa.UniqueConstraint(owner, "sequence", name="uq_livestock_trade_weight_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_livestock_trade_weight_sequence_non_negative"),
        sa.CheckConstraint("weight_kg > 0", name="ck_livestock_trade_weight_positive"),
    )
    op.create_index(f"ix_livestock_trade_weights_{owner}", "livestock_trade_weights", [owner])
    return trade, group, farm, weight


def _rows(name):
    table = sa.Table(name, sa.MetaData(), autoload_with=op.get_bind())
    return [dict(row) for row in op.get_bind().execute(sa.select(table)).mappings()]


def _replace(upgrading):
    bind = op.get_bind()
    # Start a real SQLite transaction before DDL so a failed copy is recoverable.
    # Child tables are rebuilt first/last; foreign-key enforcement stays enabled.
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")
    trades, farms, weights = (_rows(name) for name in ("livestock_trades", "livestock_trade_farms", "livestock_trade_weights"))
    groups = [] if upgrading else _rows("livestock_trade_groups")
    if upgrading:
        species = {row["id"]: row["species"] for row in _rows("animal_group_types")}
        group_ids = {}
        for trade in trades:
            group_id = str(uuid4())
            group_ids[trade["id"]] = group_id
            trade["species"] = species[trade["animal_group_type_id"]]
            group = {key: trade.pop(key) for key in ("animal_group_type_id", "price_per_kg", "price_per_head", "hair_lengths")}
            group.update(id=group_id, livestock_trade_id=trade["id"], sort_order=0,
                         head_count=trade.pop("goat_count"), label=None, notes=None,
                         created_at=trade["created_at"], updated_at=trade["updated_at"])
            groups.append(group)
        for weight in weights:
            weight["group_id"] = group_ids[weight.pop("livestock_trade_id")]
    else:
        for trade in trades:
            owned = [group for group in groups if group["livestock_trade_id"] == trade["id"]]
            if (len(owned) != 1 or trade["import_id"] or owned[0]["label"] or owned[0]["notes"]
                    or trade["pricing_model"] != ("head" if trade["species"] == "Goat" else "weight")):
                raise RuntimeError("Cannot downgrade without losing grouped/imported trade information. Restore the pre-upgrade backup instead.")
            source = owned[0]
            trade.update({key: source[key] for key in ("animal_group_type_id", "price_per_kg", "price_per_head", "hair_lengths")})
            trade["goat_count"] = source["head_count"]
            for key in ("species", "import_id", "import_payload_hash"):
                trade.pop(key)
        trade_ids = {group["id"]: group["livestock_trade_id"] for group in groups}
        for weight in weights:
            weight["livestock_trade_id"] = trade_ids[weight.pop("group_id")]
    op.drop_table("livestock_trade_weights")
    op.drop_table("livestock_trade_farms")
    if not upgrading:
        op.drop_table("livestock_trade_groups")
    op.drop_table("livestock_trades")
    trade_table, group_table, farm_table, weight_table = _create_tables(upgrading)
    for table, rows in ((trade_table, trades), (group_table, groups if upgrading else []), (farm_table, farms), (weight_table, weights)):
        if rows:
            bind.execute(table.insert(), rows)
    if bind.dialect.name == "sqlite" and bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Foreign-key check failed after migrating livestock trades")


def upgrade():
    _replace(True)


def downgrade():
    _replace(False)
