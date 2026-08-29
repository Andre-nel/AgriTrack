"""Reconcile reproductive tables and multi-farm breeding-cycle membership.

Revision ID: 8f9a0b1c2d3e
Revises: 7e8f9a0b1c2d
Create Date: 2026-08-28
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "8f9a0b1c2d3e"
down_revision = "7e8f9a0b1c2d"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _create_breeding_cycle_farms() -> None:
    op.create_table(
        "breeding_cycle_farms",
        sa.Column("cycle_id", sa.String(length=36), nullable=False),
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sort_order >= 0", name="ck_breeding_cycle_farm_sort_non_negative"
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["breeding_cycles.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "farm_id", name="uq_breeding_cycle_farm"),
        sa.UniqueConstraint(
            "cycle_id", "sort_order", name="uq_breeding_cycle_farm_sort"
        ),
    )
    op.create_index(
        op.f("ix_breeding_cycle_farms_cycle_id"),
        "breeding_cycle_farms",
        ["cycle_id"],
    )
    op.create_index(
        op.f("ix_breeding_cycle_farms_farm_id"),
        "breeding_cycle_farms",
        ["farm_id"],
    )


def _reconcile_cycle_farms() -> None:
    if "breeding_cycles" not in _table_names():
        raise RuntimeError("breeding_cycles is missing; restore the database backup before upgrading")

    has_legacy_farm = "farm_id" in _column_names("breeding_cycles")
    legacy_memberships = []
    if has_legacy_farm:
        legacy_memberships = list(
            op.get_bind().execute(
                sa.text("SELECT id, farm_id FROM breeding_cycles ORDER BY created_at, id")
            ).mappings()
        )
        if "breeding_cycle_farms" in _table_names():
            op.drop_table("breeding_cycle_farms")

        inspector = sa.inspect(op.get_bind())
        index_names = {
            row["name"] for row in inspector.get_indexes("breeding_cycles") if row["name"]
        }
        unique_names = {
            row["name"]
            for row in inspector.get_unique_constraints("breeding_cycles")
            if row["name"]
        }
        with op.batch_alter_table("breeding_cycles", recreate="always") as batch_op:
            if "ix_breeding_cycles_farm_id" in index_names:
                batch_op.drop_index("ix_breeding_cycles_farm_id")
            if "uq_breeding_cycle_farm_name" in unique_names:
                batch_op.drop_constraint(
                    "uq_breeding_cycle_farm_name", type_="unique"
                )
            batch_op.drop_column("farm_id")

    if "breeding_cycle_farms" not in _table_names():
        cycle_count = op.get_bind().execute(
            sa.text("SELECT COUNT(*) FROM breeding_cycles")
        ).scalar_one()
        if cycle_count and not legacy_memberships:
            raise RuntimeError(
                "Cannot infer farm membership for existing breeding cycles; restore a backup"
            )
        _create_breeding_cycle_farms()

    existing = {
        (str(row[0]), str(row[1]))
        for row in op.get_bind().execute(
            sa.text("SELECT cycle_id, farm_id FROM breeding_cycle_farms")
        )
    }
    for row in legacy_memberships:
        key = (str(row["id"]), str(row["farm_id"]))
        if key in existing:
            continue
        op.get_bind().execute(
            sa.text(
                """
                INSERT INTO breeding_cycle_farms (
                    cycle_id, farm_id, sort_order, id, created_at, updated_at
                ) VALUES (
                    :cycle_id, :farm_id, :sort_order, :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "cycle_id": key[0],
                "farm_id": key[1],
                "sort_order": 0,
                "id": str(uuid4()),
            },
        )


def _create_breeding_enrollments() -> None:
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
    for column in (
        "cycle_id",
        "cohort_id",
        "source_mob_id",
        "sire_cohort_id",
        "exposure_start_date",
        "exposure_end_date",
        "status",
    ):
        op.create_index(
            op.f(f"ix_breeding_enrollments_{column}"),
            "breeding_enrollments",
            [column],
        )


def _create_pregnancy_assessments() -> None:
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


def _create_parturition_outcomes() -> None:
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
    for column in (
        "enrollment_id",
        "period_start_date",
        "period_end_date",
        "supersedes_id",
        "voided_at",
    ):
        op.create_index(op.f(f"ix_parturition_outcomes_{column}"), "parturition_outcomes", [column])


def _create_parturition_stock_entries() -> None:
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
    op.create_index(
        op.f("ix_parturition_stock_entries_stock_ledger_entry_id"),
        "parturition_stock_entries",
        ["stock_ledger_entry_id"],
        unique=True,
    )


def _create_offspring_assessments() -> None:
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


def _create_reproductive_exceptions() -> None:
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


def upgrade():
    _reconcile_cycle_farms()

    creators = (
        ("breeding_enrollments", _create_breeding_enrollments),
        ("pregnancy_assessments", _create_pregnancy_assessments),
        ("parturition_outcomes", _create_parturition_outcomes),
        ("parturition_stock_entries", _create_parturition_stock_entries),
        ("offspring_assessments", _create_offspring_assessments),
        ("reproductive_exceptions", _create_reproductive_exceptions),
    )
    for table_name, creator in creators:
        if table_name not in _table_names():
            creator()


def downgrade():
    # This revision reconciles databases that may already have either form of
    # revision 7e8f9a0b1c2d. Its post-upgrade schema is the schema now declared
    # by that revision, so stepping back one revision intentionally keeps it.
    pass
