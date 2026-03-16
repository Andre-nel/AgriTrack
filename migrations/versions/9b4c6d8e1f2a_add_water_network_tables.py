"""Add water network tables.

Revision ID: 9b4c6d8e1f2a
Revises: f2c7d1a4b9e0
Create Date: 2026-03-15 18:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9b4c6d8e1f2a"
down_revision = "c7e8f9a1b2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "water_assets",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("asset_type", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("location_paddock_id", sa.String(length=36), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=11, scale=7), nullable=True),
        sa.Column("altitude_m", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column("water_level", sa.String(length=30), nullable=True),
        sa.Column("capacity_m3", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("material", sa.String(length=20), nullable=True),
        sa.Column("windmill_size_ft", sa.Integer(), nullable=True),
        sa.Column("solar_brand", sa.String(length=120), nullable=True),
        sa.Column("solar_kw", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("solar_head_m", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("trough_size", sa.String(length=20), nullable=True),
        sa.Column("source_system", sa.String(length=120), nullable=True),
        sa.Column("import_placemark_name", sa.String(length=120), nullable=True),
        sa.Column("import_style_url", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "asset_type IN ('borehole', 'pit', 'windmill', 'solarpump', 'cement_dam', 'tank', 'ground_dam', 'weir', 'trough')",
            name="ck_water_asset_type",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_water_asset_latitude_range",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_water_asset_longitude_range",
        ),
        sa.CheckConstraint(
            "capacity_m3 IS NULL OR capacity_m3 >= 0",
            name="ck_water_asset_capacity_non_negative",
        ),
        sa.CheckConstraint(
            "solar_kw IS NULL OR solar_kw >= 0",
            name="ck_water_asset_solar_kw_non_negative",
        ),
        sa.CheckConstraint(
            "solar_head_m IS NULL OR solar_head_m >= 0",
            name="ck_water_asset_solar_head_non_negative",
        ),
        sa.CheckConstraint(
            "windmill_size_ft IS NULL OR windmill_size_ft IN (10, 12, 14)",
            name="ck_water_asset_windmill_size",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["location_paddock_id"], ["paddocks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_water_assets_asset_type"), "water_assets", ["asset_type"], unique=False)
    op.create_index(op.f("ix_water_assets_farm_id"), "water_assets", ["farm_id"], unique=False)
    op.create_index(op.f("ix_water_assets_location_paddock_id"), "water_assets", ["location_paddock_id"], unique=False)

    op.create_table(
        "water_asset_served_paddocks",
        sa.Column("water_asset_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["paddock_id"], ["paddocks.id"]),
        sa.ForeignKeyConstraint(["water_asset_id"], ["water_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("water_asset_id", "paddock_id", name="uq_water_asset_served_paddock"),
    )
    op.create_index(
        op.f("ix_water_asset_served_paddocks_paddock_id"),
        "water_asset_served_paddocks",
        ["paddock_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_water_asset_served_paddocks_water_asset_id"),
        "water_asset_served_paddocks",
        ["water_asset_id"],
        unique=False,
    )

    op.create_table(
        "water_connections",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("flow_type", sa.String(length=20), nullable=False),
        sa.Column("source_asset_id", sa.String(length=36), nullable=False),
        sa.Column("destination_asset_id", sa.String(length=36), nullable=False),
        sa.Column("pump_asset_id", sa.String(length=36), nullable=True),
        sa.Column("pipe_material", sa.String(length=20), nullable=True),
        sa.Column("pipe_diameter_spec", sa.String(length=50), nullable=True),
        sa.Column("pipe_wall_thickness_spec", sa.String(length=50), nullable=True),
        sa.Column("pipe_class_spec", sa.String(length=50), nullable=True),
        sa.Column("pipe_quality_spec", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "flow_type IN ('pumped', 'gravity')",
            name="ck_water_connection_flow_type",
        ),
        sa.CheckConstraint(
            "source_asset_id <> destination_asset_id",
            name="ck_water_connection_distinct_assets",
        ),
        sa.ForeignKeyConstraint(["destination_asset_id"], ["water_assets.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["pump_asset_id"], ["water_assets.id"]),
        sa.ForeignKeyConstraint(["source_asset_id"], ["water_assets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_water_connections_destination_asset_id"), "water_connections", ["destination_asset_id"], unique=False)
    op.create_index(op.f("ix_water_connections_farm_id"), "water_connections", ["farm_id"], unique=False)
    op.create_index(op.f("ix_water_connections_flow_type"), "water_connections", ["flow_type"], unique=False)
    op.create_index(op.f("ix_water_connections_pump_asset_id"), "water_connections", ["pump_asset_id"], unique=True)
    op.create_index(op.f("ix_water_connections_source_asset_id"), "water_connections", ["source_asset_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_water_connections_source_asset_id"), table_name="water_connections")
    op.drop_index(op.f("ix_water_connections_pump_asset_id"), table_name="water_connections")
    op.drop_index(op.f("ix_water_connections_flow_type"), table_name="water_connections")
    op.drop_index(op.f("ix_water_connections_farm_id"), table_name="water_connections")
    op.drop_index(op.f("ix_water_connections_destination_asset_id"), table_name="water_connections")
    op.drop_table("water_connections")

    op.drop_index(
        op.f("ix_water_asset_served_paddocks_water_asset_id"),
        table_name="water_asset_served_paddocks",
    )
    op.drop_index(
        op.f("ix_water_asset_served_paddocks_paddock_id"),
        table_name="water_asset_served_paddocks",
    )
    op.drop_table("water_asset_served_paddocks")

    op.drop_index(op.f("ix_water_assets_location_paddock_id"), table_name="water_assets")
    op.drop_index(op.f("ix_water_assets_farm_id"), table_name="water_assets")
    op.drop_index(op.f("ix_water_assets_asset_type"), table_name="water_assets")
    op.drop_table("water_assets")
