"""Add orchard layout, individual plants and schematic irrigation.

Revision ID: e8a1c3d5f709
Revises: d7e9f1a3b5c7
"""

from alembic import op
import sqlalchemy as sa

revision = "e8a1c3d5f709"
down_revision = "d7e9f1a3b5c7"
branch_labels = None
depends_on = None


def _identity():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _owner(name, target, ondelete="CASCADE", **kwargs):
    return sa.Column(name, sa.String(36), sa.ForeignKey(target, ondelete=ondelete), nullable=False, **kwargs)


def upgrade():
    op.create_table("orchards", *_identity(),
        _owner("farm_id", "farms.id"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("seed_key", sa.String(100)),
        sa.UniqueConstraint("farm_id", "seed_key", name="uq_orchard_seed"),
        sa.CheckConstraint("revision > 0", name="ck_orchard_revision"),
    )
    op.create_table("orchard_rows", *_identity(),
        _owner("orchard_id", "orchards.id"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("orientation", sa.String(10), nullable=False),
        sa.Column("x_m", sa.Float(), nullable=False),
        sa.Column("y_m", sa.Float(), nullable=False),
        sa.Column("spacing_m", sa.Float(), nullable=False),
        sa.CheckConstraint("orientation IN ('horizontal', 'vertical')", name="ck_orchard_row_orientation"),
        sa.CheckConstraint("spacing_m > 0", name="ck_orchard_row_spacing"),
    )
    op.create_table("orchard_spaces", *_identity(),
        _owner("row_id", "orchard_rows.id"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("row_id", "position", name="uq_orchard_space_position"),
    )
    op.create_table("orchard_plants", *_identity(),
        _owner("orchard_id", "orchards.id"),
        sa.Column("space_id", sa.String(36), sa.ForeignKey("orchard_spaces.id", ondelete="RESTRICT"), unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("purchased_on", sa.Date()),
        sa.Column("supplier", sa.String(160)),
        sa.Column("planted_on", sa.Date()),
        sa.CheckConstraint("(status = 'unassigned' AND space_id IS NULL) OR (status IN ('planned', 'planted') AND space_id IS NOT NULL)", name="ck_orchard_plant_placement"),
        sa.CheckConstraint("planted_on IS NULL OR status = 'planted'", name="ck_orchard_plant_date"),
    )
    op.create_table("orchard_lines", *_identity(),
        _owner("orchard_id", "orchards.id"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("vertices", sa.JSON(), nullable=False),
        sa.Column("schematic", sa.Boolean(), nullable=False),
    )
    op.create_table("orchard_emitters", *_identity(),
        _owner("line_id", "orchard_lines.id"),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("x_m", sa.Float(), nullable=False),
        sa.Column("y_m", sa.Float(), nullable=False),
        sa.CheckConstraint("kind IN ('dripper', 'sprinkler')", name="ck_orchard_emitter_kind"),
    )
    for table, column in [
        ("orchards", "farm_id"), ("orchard_rows", "orchard_id"),
        ("orchard_spaces", "row_id"), ("orchard_plants", "orchard_id"),
        ("orchard_lines", "orchard_id"), ("orchard_emitters", "line_id"),
    ]:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    # Plants must go before the spaces whose deletion they restrict.
    for table in ("orchard_emitters", "orchard_lines", "orchard_plants", "orchard_spaces", "orchard_rows", "orchards"):
        op.drop_table(table)
