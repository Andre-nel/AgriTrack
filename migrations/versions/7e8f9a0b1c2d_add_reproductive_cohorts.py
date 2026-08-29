"""Add persistent animal cohorts and reproductive lifecycle tables.

Revision ID: 7e8f9a0b1c2d
Revises: 6d4a8c1f2b90
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa


revision = "7e8f9a0b1c2d"
down_revision = "6d4a8c1f2b90"
branch_labels = None
depends_on = None


def _uuid() -> str:
    return str(uuid.uuid4())


def upgrade():
    op.create_table(
        "animal_cohorts",
        sa.Column("animal_group_type_id", sa.String(length=36), nullable=False),
        sa.Column("origin_farm_id", sa.String(length=36), nullable=True),
        sa.Column("origin", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "reproductive_state", sa.String(length=30), nullable=False, server_default="not_recorded"
        ),
        sa.Column(
            "expected_litter_size", sa.String(length=20), nullable=False, server_default="not_recorded"
        ),
        sa.Column(
            "lactation_state", sa.String(length=20), nullable=False, server_default="not_recorded"
        ),
        sa.Column(
            "offspring_at_foot", sa.String(length=20), nullable=False, server_default="not_recorded"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "origin IN ('legacy', 'manual', 'purchase', 'birth', 'split')",
            name="ck_animal_cohort_origin_allowed",
        ),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_animal_cohort_status_allowed"),
        sa.CheckConstraint(
            "reproductive_state IN ('not_recorded', 'with_sire', 'awaiting_scan', "
            "'pregnant', 'not_pregnant', 'parturated')",
            name="ck_animal_cohort_reproductive_state_allowed",
        ),
        sa.CheckConstraint(
            "expected_litter_size IN ('not_recorded', 'unknown', 'single', 'twins', 'multiple')",
            name="ck_animal_cohort_litter_size_allowed",
        ),
        sa.CheckConstraint(
            "lactation_state IN ('not_recorded', 'lactating', 'dry')",
            name="ck_animal_cohort_lactation_state_allowed",
        ),
        sa.CheckConstraint(
            "offspring_at_foot IN ('not_recorded', 'none', 'single', 'twins', 'multiple')",
            name="ck_animal_cohort_offspring_at_foot_allowed",
        ),
        sa.ForeignKeyConstraint(["animal_group_type_id"], ["animal_group_types.id"]),
        sa.ForeignKeyConstraint(["origin_farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_animal_cohorts_animal_group_type_id"), "animal_cohorts", ["animal_group_type_id"])
    op.create_index(op.f("ix_animal_cohorts_origin_farm_id"), "animal_cohorts", ["origin_farm_id"])
    op.create_index(op.f("ix_animal_cohorts_status"), "animal_cohorts", ["status"])
    op.create_index(op.f("ix_animal_cohorts_reproductive_state"), "animal_cohorts", ["reproductive_state"])
    op.create_index(op.f("ix_animal_cohorts_expected_litter_size"), "animal_cohorts", ["expected_litter_size"])
    op.create_index(op.f("ix_animal_cohorts_lactation_state"), "animal_cohorts", ["lactation_state"])
    op.create_index(op.f("ix_animal_cohorts_offspring_at_foot"), "animal_cohorts", ["offspring_at_foot"])

    with op.batch_alter_table("animal_group_balances") as batch_op:
        batch_op.add_column(sa.Column("cohort_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_animal_group_balances_cohort_id_animal_cohorts",
            "animal_cohorts",
            ["cohort_id"],
            ["id"],
        )
        batch_op.create_index(op.f("ix_animal_group_balances_cohort_id"), ["cohort_id"], unique=False)

    bind = op.get_bind()
    balance_rows = bind.execute(
        sa.text(
            """
            SELECT b.id, b.mob_id, b.animal_group_type_id, m.farm_id
            FROM animal_group_balances b
            JOIN mobs m ON m.id = b.mob_id
            """
        )
    ).mappings().all()
    now = datetime.now(timezone.utc)
    cohort_by_mob_group = {}
    for row in balance_rows:
        cohort_id = _uuid()
        cohort_by_mob_group[(row["mob_id"], row["animal_group_type_id"])] = cohort_id
        bind.execute(
            sa.text(
                """
                INSERT INTO animal_cohorts (
                    id, animal_group_type_id, origin_farm_id, origin, status,
                    reproductive_state, expected_litter_size, offspring_at_foot,
                    lactation_state, created_at, updated_at
                ) VALUES (
                    :id, :group_id, :farm_id, 'legacy', 'active',
                    'not_recorded', 'not_recorded', 'not_recorded',
                    'not_recorded', :now, :now
                )
                """
            ),
            {
                "id": cohort_id,
                "group_id": row["animal_group_type_id"],
                "farm_id": row["farm_id"],
                "now": now,
            },
        )
        bind.execute(
            sa.text("UPDATE animal_group_balances SET cohort_id = :cohort_id WHERE id = :balance_id"),
            {"cohort_id": cohort_id, "balance_id": row["id"]},
        )

    with op.batch_alter_table("animal_group_balances") as batch_op:
        batch_op.drop_constraint("uq_mob_group_balance", type_="unique")
        batch_op.create_unique_constraint("uq_mob_cohort_balance", ["mob_id", "cohort_id"])
    op.create_index(
        "uq_mob_legacy_group_balance",
        "animal_group_balances",
        ["mob_id", "animal_group_type_id"],
        unique=True,
        sqlite_where=sa.text("cohort_id IS NULL"),
        postgresql_where=sa.text("cohort_id IS NULL"),
    )

    with op.batch_alter_table("stock_ledger_entries") as batch_op:
        batch_op.add_column(sa.Column("cohort_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_stock_ledger_entries_cohort_id_animal_cohorts",
            "animal_cohorts",
            ["cohort_id"],
            ["id"],
        )
        batch_op.create_index(op.f("ix_stock_ledger_entries_cohort_id"), ["cohort_id"], unique=False)
    for (mob_id, group_id), cohort_id in cohort_by_mob_group.items():
        bind.execute(
            sa.text(
                """
                UPDATE stock_ledger_entries
                SET cohort_id = :cohort_id
                WHERE mob_id = :mob_id AND animal_group_type_id = :group_id
                """
            ),
            {"cohort_id": cohort_id, "mob_id": mob_id, "group_id": group_id},
        )

    op.create_table(
        "animal_cohort_lineage",
        sa.Column("parent_cohort_id", sa.String(length=36), nullable=False),
        sa.Column("child_cohort_id", sa.String(length=36), nullable=False),
        sa.Column("movement_event_id", sa.String(length=36), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_animal_cohort_lineage_quantity_positive"),
        sa.CheckConstraint(
            "reason IN ('stock_split', 'reproductive_partition', 'correction')",
            name="ck_animal_cohort_lineage_reason_allowed",
        ),
        sa.CheckConstraint("parent_cohort_id <> child_cohort_id", name="ck_animal_cohort_lineage_distinct"),
        sa.ForeignKeyConstraint(["parent_cohort_id"], ["animal_cohorts.id"]),
        sa.ForeignKeyConstraint(["child_cohort_id"], ["animal_cohorts.id"]),
        sa.ForeignKeyConstraint(["movement_event_id"], ["movement_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_cohort_id", "child_cohort_id", name="uq_animal_cohort_lineage_edge"),
    )
    op.create_index(op.f("ix_animal_cohort_lineage_parent_cohort_id"), "animal_cohort_lineage", ["parent_cohort_id"])
    op.create_index(op.f("ix_animal_cohort_lineage_child_cohort_id"), "animal_cohort_lineage", ["child_cohort_id"])
    op.create_index(op.f("ix_animal_cohort_lineage_movement_event_id"), "animal_cohort_lineage", ["movement_event_id"])

    op.create_table(
        "female_status_observations",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("mob_id", sa.String(length=36), nullable=False),
        sa.Column("source_cohort_id", sa.String(length=36), nullable=False),
        sa.Column("result_cohort_id", sa.String(length=36), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reproductive_state", sa.String(length=30), nullable=False),
        sa.Column("expected_litter_size", sa.String(length=20), nullable=False),
        sa.Column("lactation_state", sa.String(length=20), nullable=False),
        sa.Column("offspring_at_foot", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_female_status_observation_quantity_positive"),
        sa.CheckConstraint(
            "reproductive_state IN ('not_recorded', 'with_sire', 'awaiting_scan', "
            "'pregnant', 'not_pregnant', 'parturated')",
            name="ck_female_status_observation_reproductive_state_allowed",
        ),
        sa.CheckConstraint(
            "expected_litter_size IN ('not_recorded', 'unknown', 'single', 'twins', 'multiple')",
            name="ck_female_status_observation_litter_size_allowed",
        ),
        sa.CheckConstraint(
            "lactation_state IN ('not_recorded', 'lactating', 'dry')",
            name="ck_female_status_observation_lactation_state_allowed",
        ),
        sa.CheckConstraint(
            "offspring_at_foot IN ('not_recorded', 'none', 'single', 'twins', 'multiple')",
            name="ck_female_status_observation_offspring_at_foot_allowed",
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["mob_id"], ["mobs.id"]),
        sa.ForeignKeyConstraint(["source_cohort_id"], ["animal_cohorts.id"]),
        sa.ForeignKeyConstraint(["result_cohort_id"], ["animal_cohorts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "farm_id",
        "mob_id",
        "source_cohort_id",
        "result_cohort_id",
        "observed_on",
    ):
        op.create_index(op.f(f"ix_female_status_observations_{column}"), "female_status_observations", [column])

    op.create_table(
        "breeding_cycles",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("species", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("exposure_start_date", sa.Date(), nullable=False),
        sa.Column("exposure_end_date", sa.Date(), nullable=True),
        sa.Column("expected_parturition_start_date", sa.Date(), nullable=True),
        sa.Column("expected_parturition_end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("species IN ('Cattle', 'Sheep', 'Goat')", name="ck_breeding_cycle_species_allowed"),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_breeding_cycle_status_allowed"),
        sa.CheckConstraint(
            "exposure_end_date IS NULL OR exposure_end_date >= exposure_start_date",
            name="ck_breeding_cycle_exposure_date_order",
        ),
        sa.CheckConstraint(
            "expected_parturition_end_date IS NULL OR expected_parturition_start_date IS NULL "
            "OR expected_parturition_end_date >= expected_parturition_start_date",
            name="ck_breeding_cycle_parturition_date_order",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("species", "status", "exposure_start_date", "exposure_end_date"):
        op.create_index(op.f(f"ix_breeding_cycles_{column}"), "breeding_cycles", [column])

    op.create_table(
        "breeding_cycle_farms",
        sa.Column("cycle_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sort_order >= 0", name="ck_breeding_cycle_farm_sort_non_negative"),
        sa.ForeignKeyConstraint(["cycle_id"], ["breeding_cycles.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "farm_id", name="uq_breeding_cycle_farm"),
        sa.UniqueConstraint("cycle_id", "sort_order", name="uq_breeding_cycle_farm_sort"),
    )
    op.create_index(op.f("ix_breeding_cycle_farms_cycle_id"), "breeding_cycle_farms", ["cycle_id"])
    op.create_index(op.f("ix_breeding_cycle_farms_farm_id"), "breeding_cycle_farms", ["farm_id"])

    op.create_table(
        "breeding_enrollments",
        sa.Column("cycle_id", sa.String(length=36), nullable=False),
        sa.Column("cohort_id", sa.String(length=36), nullable=False),
        sa.Column("source_mob_id", sa.String(length=36), nullable=False),
        sa.Column("sire_cohort_id", sa.String(length=36), nullable=True),
        sa.Column("exposed_count", sa.Integer(), nullable=False),
        sa.Column("exposure_start_date", sa.Date(), nullable=False),
        sa.Column("exposure_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("exposed_count > 0", name="ck_breeding_enrollment_exposed_positive"),
        sa.CheckConstraint(
            "exposure_end_date IS NULL OR exposure_end_date >= exposure_start_date",
            name="ck_breeding_enrollment_exposure_date_order",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'voided')",
            name="ck_breeding_enrollment_status_allowed",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["breeding_cycles.id"]),
        sa.ForeignKeyConstraint(["cohort_id"], ["animal_cohorts.id"]),
        sa.ForeignKeyConstraint(["sire_cohort_id"], ["animal_cohorts.id"]),
        sa.ForeignKeyConstraint(["source_mob_id"], ["mobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "cohort_id", name="uq_breeding_enrollment_cycle_cohort"),
    )
    for column in ("cycle_id", "cohort_id", "source_mob_id", "sire_cohort_id", "exposure_start_date", "exposure_end_date", "status"):
        op.create_index(op.f(f"ix_breeding_enrollments_{column}"), "breeding_enrollments", [column])

    op.create_table(
        "pregnancy_assessments",
        sa.Column("enrollment_id", sa.String(length=36), nullable=False),
        sa.Column("assessed_on", sa.Date(), nullable=False),
        sa.Column("pregnant_count", sa.Integer(), nullable=False),
        sa.Column("not_pregnant_count", sa.Integer(), nullable=False),
        sa.Column("unassessed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_single_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_twin_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_multiple_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_unknown_litter_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_offspring_total", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "pregnant_count >= 0 AND not_pregnant_count >= 0 AND unassessed_count >= 0",
            name="ck_pregnancy_assessment_counts_non_negative",
        ),
        sa.CheckConstraint(
            "expected_single_count >= 0 AND expected_twin_count >= 0 "
            "AND expected_multiple_count >= 0 AND expected_unknown_litter_count >= 0",
            name="ck_pregnancy_assessment_litters_non_negative",
        ),
        sa.CheckConstraint(
            "expected_offspring_total IS NULL OR expected_offspring_total >= 0",
            name="ck_pregnancy_assessment_expected_total_non_negative",
        ),
        sa.ForeignKeyConstraint(["enrollment_id"], ["breeding_enrollments.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["pregnancy_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("enrollment_id", "assessed_on", "supersedes_id", "voided_at"):
        op.create_index(op.f(f"ix_pregnancy_assessments_{column}"), "pregnancy_assessments", [column])

    op.create_table(
        "parturition_outcomes",
        sa.Column("enrollment_id", sa.String(length=36), nullable=False),
        sa.Column("period_start_date", sa.Date(), nullable=False),
        sa.Column("period_end_date", sa.Date(), nullable=True),
        sa.Column("females_parturated_count", sa.Integer(), nullable=False),
        sa.Column("single_parturition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("twin_parturition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("multiple_parturition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_litter_parturition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("offspring_born_total", sa.Integer(), nullable=False),
        sa.Column("offspring_born_alive", sa.Integer(), nullable=False),
        sa.Column("offspring_stillborn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strong_at_birth_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unassessed_at_birth_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "period_end_date IS NULL OR period_end_date >= period_start_date",
            name="ck_parturition_outcome_date_order",
        ),
        sa.CheckConstraint(
            "females_parturated_count >= 0 AND single_parturition_count >= 0 "
            "AND twin_parturition_count >= 0 AND multiple_parturition_count >= 0 "
            "AND unknown_litter_parturition_count >= 0",
            name="ck_parturition_outcome_female_counts_non_negative",
        ),
        sa.CheckConstraint(
            "offspring_born_total >= 0 AND offspring_born_alive >= 0 "
            "AND offspring_stillborn >= 0 AND strong_at_birth_count >= 0 "
            "AND unassessed_at_birth_count >= 0",
            name="ck_parturition_outcome_offspring_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(["enrollment_id"], ["breeding_enrollments.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["parturition_outcomes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("enrollment_id", "period_start_date", "period_end_date", "supersedes_id", "voided_at"):
        op.create_index(op.f(f"ix_parturition_outcomes_{column}"), "parturition_outcomes", [column])

    op.create_table(
        "parturition_stock_entries",
        sa.Column("outcome_id", sa.String(length=36), nullable=False),
        sa.Column("stock_ledger_entry_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["outcome_id"], ["parturition_outcomes.id"]),
        sa.ForeignKeyConstraint(["stock_ledger_entry_id"], ["stock_ledger_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outcome_id", "stock_ledger_entry_id", name="uq_parturition_stock_entry"),
        sa.UniqueConstraint("stock_ledger_entry_id"),
    )
    op.create_index(op.f("ix_parturition_stock_entries_outcome_id"), "parturition_stock_entries", ["outcome_id"])
    op.create_index(op.f("ix_parturition_stock_entries_stock_ledger_entry_id"), "parturition_stock_entries", ["stock_ledger_entry_id"], unique=True)

    op.create_table(
        "offspring_assessments",
        sa.Column("enrollment_id", sa.String(length=36), nullable=False),
        sa.Column("assessed_on", sa.Date(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("present_count", sa.Integer(), nullable=False),
        sa.Column("assessed_count", sa.Integer(), nullable=False),
        sa.Column("strong_count", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("stage IN ('marking', 'weaning', 'other')", name="ck_offspring_assessment_stage_allowed"),
        sa.CheckConstraint(
            "present_count >= 0 AND assessed_count >= 0 AND strong_count >= 0",
            name="ck_offspring_assessment_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(["enrollment_id"], ["breeding_enrollments.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["offspring_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("enrollment_id", "assessed_on", "stage", "supersedes_id", "voided_at"):
        op.create_index(op.f(f"ix_offspring_assessments_{column}"), "offspring_assessments", [column])

    op.create_table(
        "reproductive_exceptions",
        sa.Column("enrollment_id", sa.String(length=36), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('pregnancy_loss', 'offspring_reassignment', 'other')",
            name="ck_reproductive_exception_type_allowed",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_reproductive_exception_quantity_positive"),
        sa.ForeignKeyConstraint(["enrollment_id"], ["breeding_enrollments.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["reproductive_exceptions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("enrollment_id", "observed_on", "event_type", "supersedes_id", "voided_at"):
        op.create_index(op.f(f"ix_reproductive_exceptions_{column}"), "reproductive_exceptions", [column])


def downgrade():
    duplicate_count = op.get_bind().execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT mob_id, animal_group_type_id
                FROM animal_group_balances
                GROUP BY mob_id, animal_group_type_id
                HAVING COUNT(*) > 1
            ) duplicates
            """
        )
    ).scalar()
    if duplicate_count:
        raise RuntimeError(
            "Cannot downgrade while a mob contains multiple cohorts for one animal group type."
        )

    for table in (
        "reproductive_exceptions",
        "offspring_assessments",
        "parturition_stock_entries",
        "parturition_outcomes",
        "pregnancy_assessments",
        "breeding_enrollments",
        "breeding_cycle_farms",
        "breeding_cycles",
        "female_status_observations",
        "animal_cohort_lineage",
    ):
        op.drop_table(table)

    with op.batch_alter_table("stock_ledger_entries") as batch_op:
        batch_op.drop_index(op.f("ix_stock_ledger_entries_cohort_id"))
        batch_op.drop_constraint(
            "fk_stock_ledger_entries_cohort_id_animal_cohorts", type_="foreignkey"
        )
        batch_op.drop_column("cohort_id")

    op.drop_index("uq_mob_legacy_group_balance", table_name="animal_group_balances")
    with op.batch_alter_table("animal_group_balances") as batch_op:
        batch_op.drop_constraint("uq_mob_cohort_balance", type_="unique")
        batch_op.create_unique_constraint(
            "uq_mob_group_balance", ["mob_id", "animal_group_type_id"]
        )
        batch_op.drop_index(op.f("ix_animal_group_balances_cohort_id"))
        batch_op.drop_constraint(
            "fk_animal_group_balances_cohort_id_animal_cohorts", type_="foreignkey"
        )
        batch_op.drop_column("cohort_id")

    op.drop_table("animal_cohorts")
