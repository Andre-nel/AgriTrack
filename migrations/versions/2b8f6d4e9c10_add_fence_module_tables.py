"""Add fence module tables.

Revision ID: 2b8f6d4e9c10
Revises: 0f4c2d8e9a31
Create Date: 2026-06-12 21:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "2b8f6d4e9c10"
down_revision = "0f4c2d8e9a31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fence_sections",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("section_key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("section_type", sa.String(length=20), nullable=False),
        sa.Column("paddock_a_id", sa.String(length=36), nullable=False),
        sa.Column("paddock_b_id", sa.String(length=36), nullable=True),
        sa.Column("length_m", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("geometry_json", sa.Text(), nullable=True),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("height_profile", sa.String(length=20), nullable=False),
        sa.Column("construction_type", sa.String(length=40), nullable=False),
        sa.Column("post_type", sa.String(length=40), nullable=False),
        sa.Column("dropper_type", sa.String(length=40), nullable=False),
        sa.Column("wire_type", sa.String(length=40), nullable=False),
        sa.Column("mesh_type", sa.String(length=40), nullable=False),
        sa.Column("electric_wire", sa.Boolean(), nullable=False),
        sa.Column("electric_wire_type", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("holds_cattle", sa.String(length=20), nullable=False),
        sa.Column("holds_sheep", sa.String(length=20), nullable=False),
        sa.Column("holds_goats", sa.String(length=20), nullable=False),
        sa.Column("excludes_jackal", sa.String(length=20), nullable=False),
        sa.Column("excludes_predators", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_fence_sections_source"),
        sa.CheckConstraint("section_type IN ('boundary', 'internal')", name="ck_fence_sections_type"),
        sa.CheckConstraint(
            "(section_type = 'boundary' AND paddock_b_id IS NULL) "
            "OR (section_type = 'internal' AND paddock_b_id IS NOT NULL AND paddock_a_id < paddock_b_id)",
            name="ck_fence_sections_paddock_shape",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["paddock_a_id"], ["paddocks.id"]),
        sa.ForeignKeyConstraint(["paddock_b_id"], ["paddocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "section_key", name="uq_fence_sections_farm_key"),
    )
    op.create_index(op.f("ix_fence_sections_active"), "fence_sections", ["active"], unique=False)
    op.create_index(op.f("ix_fence_sections_condition"), "fence_sections", ["condition"], unique=False)
    op.create_index(op.f("ix_fence_sections_farm_id"), "fence_sections", ["farm_id"], unique=False)
    op.create_index(op.f("ix_fence_sections_paddock_a_id"), "fence_sections", ["paddock_a_id"], unique=False)
    op.create_index(op.f("ix_fence_sections_paddock_b_id"), "fence_sections", ["paddock_b_id"], unique=False)

    op.create_table(
        "fence_events",
        sa.Column("fence_section_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column(
            "event_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("condition_after", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint("event_type IN ('note', 'inspection', 'maintenance')", name="ck_fence_events_type"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["fence_section_id"], ["fence_sections.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_fence_events_event_at"), "fence_events", ["event_at"], unique=False)
    op.create_index(op.f("ix_fence_events_event_type"), "fence_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_fence_events_farm_id"), "fence_events", ["farm_id"], unique=False)
    op.create_index(op.f("ix_fence_events_fence_section_id"), "fence_events", ["fence_section_id"], unique=False)

    op.create_table(
        "fence_event_materials",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("material_type", sa.String(length=40), nullable=False),
        sa.Column("material_detail", sa.String(length=160), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "action IN ('replaced', 'installed', 'repaired', 'packed', 'removed')",
            name="ck_fence_event_materials_action",
        ),
        sa.CheckConstraint(
            "material_type IN ('mesh', 'wire', 'pole', 'dropper', 'electric_wire', 'stone', 'other')",
            name="ck_fence_event_materials_type",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity >= 0",
            name="ck_fence_event_materials_quantity_non_negative",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["fence_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_fence_event_materials_event_id"), "fence_event_materials", ["event_id"], unique=False)

    with op.batch_alter_table("note_attachments") as batch:
        batch.add_column(sa.Column("fence_event_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_note_attachments_fence_event_id_fence_events",
            "fence_events",
            ["fence_event_id"],
            ["id"],
        )
        batch.drop_constraint("ck_note_attachments_one_event", type_="check")
        batch.create_check_constraint(
            "ck_note_attachments_one_event",
            "(mob_event_id IS NOT NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL AND fence_event_id IS NULL) "
            "OR (mob_event_id IS NULL AND paddock_event_id IS NOT NULL AND water_asset_event_id IS NULL AND fence_event_id IS NULL) "
            "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NOT NULL AND fence_event_id IS NULL) "
            "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL AND fence_event_id IS NOT NULL)",
        )
        batch.create_index("ix_note_attachments_fence_event_id", ["fence_event_id"])

    with op.batch_alter_table("task_entity_links") as batch:
        batch.add_column(sa.Column("fence_section_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_task_entity_links_fence_section_id_fence_sections",
            "fence_sections",
            ["fence_section_id"],
            ["id"],
        )
        batch.drop_constraint("ck_task_entity_links_one_entity", type_="check")
        batch.create_check_constraint(
            "ck_task_entity_links_one_entity",
            "(paddock_id IS NOT NULL AND water_asset_id IS NULL AND mob_id IS NULL AND fence_section_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NOT NULL AND mob_id IS NULL AND fence_section_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NULL AND mob_id IS NOT NULL AND fence_section_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NULL AND mob_id IS NULL AND fence_section_id IS NOT NULL)",
        )
        batch.create_unique_constraint(
            "uq_task_entity_links_task_fence_section",
            ["task_id", "fence_section_id"],
        )
        batch.create_index("ix_task_entity_links_fence_section_id", ["fence_section_id"])


def downgrade():
    with op.batch_alter_table("task_entity_links") as batch:
        batch.drop_index("ix_task_entity_links_fence_section_id")
        batch.drop_constraint("uq_task_entity_links_task_fence_section", type_="unique")
        batch.drop_constraint("fk_task_entity_links_fence_section_id_fence_sections", type_="foreignkey")
        batch.drop_constraint("ck_task_entity_links_one_entity", type_="check")
        batch.create_check_constraint(
            "ck_task_entity_links_one_entity",
            "(paddock_id IS NOT NULL AND water_asset_id IS NULL AND mob_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NOT NULL AND mob_id IS NULL) "
            "OR (paddock_id IS NULL AND water_asset_id IS NULL AND mob_id IS NOT NULL)",
        )
        batch.drop_column("fence_section_id")

    with op.batch_alter_table("note_attachments") as batch:
        batch.drop_index("ix_note_attachments_fence_event_id")
        batch.drop_constraint("fk_note_attachments_fence_event_id_fence_events", type_="foreignkey")
        batch.drop_constraint("ck_note_attachments_one_event", type_="check")
        batch.create_check_constraint(
            "ck_note_attachments_one_event",
            "(mob_event_id IS NOT NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NULL) "
            "OR (mob_event_id IS NULL AND paddock_event_id IS NOT NULL AND water_asset_event_id IS NULL) "
            "OR (mob_event_id IS NULL AND paddock_event_id IS NULL AND water_asset_event_id IS NOT NULL)",
        )
        batch.drop_column("fence_event_id")

    op.drop_index(op.f("ix_fence_event_materials_event_id"), table_name="fence_event_materials")
    op.drop_table("fence_event_materials")
    op.drop_index(op.f("ix_fence_events_fence_section_id"), table_name="fence_events")
    op.drop_index(op.f("ix_fence_events_farm_id"), table_name="fence_events")
    op.drop_index(op.f("ix_fence_events_event_type"), table_name="fence_events")
    op.drop_index(op.f("ix_fence_events_event_at"), table_name="fence_events")
    op.drop_table("fence_events")
    op.drop_index(op.f("ix_fence_sections_paddock_b_id"), table_name="fence_sections")
    op.drop_index(op.f("ix_fence_sections_paddock_a_id"), table_name="fence_sections")
    op.drop_index(op.f("ix_fence_sections_farm_id"), table_name="fence_sections")
    op.drop_index(op.f("ix_fence_sections_condition"), table_name="fence_sections")
    op.drop_index(op.f("ix_fence_sections_active"), table_name="fence_sections")
    op.drop_table("fence_sections")
