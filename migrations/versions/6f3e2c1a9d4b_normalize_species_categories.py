"""Normalize species values and constrain allowed categories.

Revision ID: 6f3e2c1a9d4b
Revises: 27248a9babc8
Create Date: 2026-02-27 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal


# revision identifiers, used by Alembic.
revision = "6f3e2c1a9d4b"
down_revision = "27248a9babc8"
branch_labels = None
depends_on = None


SPECIES_MAP = {
    "cattle": "Cattle",
    "sheep": "Sheep",
    "goat": "Goat",
}


def _merge_balance_rows(conn, source_group_id: str, target_group_id: str) -> None:
    rows = conn.execute(
        sa.text(
            """
            SELECT id, mob_id, head_count
            FROM animal_group_balances
            WHERE animal_group_type_id = :group_id
            """
        ),
        {"group_id": source_group_id},
    ).mappings()

    for row in rows:
        target = conn.execute(
            sa.text(
                """
                SELECT id, head_count
                FROM animal_group_balances
                WHERE animal_group_type_id = :group_id AND mob_id = :mob_id
                """
            ),
            {"group_id": target_group_id, "mob_id": row["mob_id"]},
        ).mappings().first()

        if target:
            conn.execute(
                sa.text(
                    """
                    UPDATE animal_group_balances
                    SET head_count = :head_count
                    WHERE id = :id
                    """
                ),
                {"id": target["id"], "head_count": int(target["head_count"]) + int(row["head_count"])},
            )
            conn.execute(
                sa.text("DELETE FROM animal_group_balances WHERE id = :id"),
                {"id": row["id"]},
            )
            continue

        conn.execute(
            sa.text(
                """
                UPDATE animal_group_balances
                SET animal_group_type_id = :target_group_id
                WHERE id = :id
                """
            ),
            {"target_group_id": target_group_id, "id": row["id"]},
        )


def _merge_snapshot_rows(conn, source_group_id: str, target_group_id: str) -> None:
    rows = conn.execute(
        sa.text(
            """
            SELECT id, farm_id, snapshot_date, paddock_id, head_count
            FROM daily_stock_snapshots
            WHERE animal_group_type_id = :group_id
            """
        ),
        {"group_id": source_group_id},
    ).mappings()

    for row in rows:
        if row["paddock_id"] is None:
            target = conn.execute(
                sa.text(
                    """
                    SELECT id, head_count
                    FROM daily_stock_snapshots
                    WHERE animal_group_type_id = :group_id
                      AND farm_id = :farm_id
                      AND snapshot_date = :snapshot_date
                      AND paddock_id IS NULL
                    """
                ),
                {
                    "group_id": target_group_id,
                    "farm_id": row["farm_id"],
                    "snapshot_date": row["snapshot_date"],
                },
            ).mappings().first()
        else:
            target = conn.execute(
                sa.text(
                    """
                    SELECT id, head_count
                    FROM daily_stock_snapshots
                    WHERE animal_group_type_id = :group_id
                      AND farm_id = :farm_id
                      AND snapshot_date = :snapshot_date
                      AND paddock_id = :paddock_id
                    """
                ),
                {
                    "group_id": target_group_id,
                    "farm_id": row["farm_id"],
                    "snapshot_date": row["snapshot_date"],
                    "paddock_id": row["paddock_id"],
                },
            ).mappings().first()

        if target:
            conn.execute(
                sa.text(
                    """
                    UPDATE daily_stock_snapshots
                    SET head_count = :head_count
                    WHERE id = :id
                    """
                ),
                {
                    "id": target["id"],
                    "head_count": Decimal(str(target["head_count"])) + Decimal(str(row["head_count"])),
                },
            )
            conn.execute(
                sa.text("DELETE FROM daily_stock_snapshots WHERE id = :id"),
                {"id": row["id"]},
            )
            continue

        conn.execute(
            sa.text(
                """
                UPDATE daily_stock_snapshots
                SET animal_group_type_id = :target_group_id
                WHERE id = :id
                """
            ),
            {"target_group_id": target_group_id, "id": row["id"]},
        )


def _merge_group_type(conn, source_group_id: str, target_group_id: str) -> None:
    _merge_balance_rows(conn, source_group_id=source_group_id, target_group_id=target_group_id)
    _merge_snapshot_rows(conn, source_group_id=source_group_id, target_group_id=target_group_id)

    conn.execute(
        sa.text(
            """
            UPDATE stock_ledger_entries
            SET animal_group_type_id = :target_group_id
            WHERE animal_group_type_id = :source_group_id
            """
        ),
        {"target_group_id": target_group_id, "source_group_id": source_group_id},
    )
    conn.execute(
        sa.text("DELETE FROM animal_group_types WHERE id = :source_group_id"),
        {"source_group_id": source_group_id},
    )


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, species, breed, sex, age_class
            FROM animal_group_types
            """
        )
    ).mappings().all()

    invalid_species = set()
    grouped_rows = {}
    for row in rows:
        species_key = (row["species"] or "").strip().lower()
        normalized_species = SPECIES_MAP.get(species_key)
        if normalized_species is None:
            invalid_species.add(row["species"])
            continue

        key = (normalized_species, row["breed"], row["sex"], row["age_class"])
        grouped_rows.setdefault(key, []).append(row)

    if invalid_species:
        species_list = ", ".join(sorted(str(v) for v in invalid_species))
        raise RuntimeError(
            f"Unsupported species values found in animal_group_types: {species_list}. "
            "Only Cattle, Sheep, Goat are supported."
        )

    for (normalized_species, _, _, _), group_rows in grouped_rows.items():
        canonical = next((r for r in group_rows if r["species"] == normalized_species), group_rows[0])

        if canonical["species"] != normalized_species:
            conn.execute(
                sa.text(
                    """
                    UPDATE animal_group_types
                    SET species = :species
                    WHERE id = :id
                    """
                ),
                {"species": normalized_species, "id": canonical["id"]},
            )

        for row in group_rows:
            if row["id"] == canonical["id"]:
                continue
            _merge_group_type(conn, source_group_id=row["id"], target_group_id=canonical["id"])

    with op.batch_alter_table("animal_group_types", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_animal_group_species_allowed",
            "species IN ('Cattle', 'Sheep', 'Goat')",
        )


def downgrade():
    with op.batch_alter_table("animal_group_types", schema=None) as batch_op:
        batch_op.drop_constraint("ck_animal_group_species_allowed", type_="check")
