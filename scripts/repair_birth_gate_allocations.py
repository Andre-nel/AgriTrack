"""Preview or apply the scoped Lam Bokke birth-allocation correction.

Run without arguments to inspect the proposed correction. ``--apply`` takes an
unpruned backup and changes live allocation/history only, at the repair time.
It does not replay births or change earlier grazing history or stock records.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import current_app  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.config import get_config  # noqa: E402
from app.core.sqlite import sqlite_database_path  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    AnimalGroupType,
    GrazingAllocationGroupAssignment,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    MobEvent,
    Paddock,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType  # noqa: E402
from app.services.allocation_distribution_service import AllocationDistributionService  # noqa: E402
from app.services.gate_service import GateService  # noqa: E402
from app.services.grazing_history_service import GrazingHistoryService  # noqa: E402
from app.services.mob_event_service import MobEventService  # noqa: E402
from scripts.backup_agritrack import create_backup  # noqa: E402

MOB_ID = "9a82709f-6364-4315-9ec1-7127a680dbb9"
FARM_ID = "50356fed-db04-479f-88af-3f717b2cfcff"
KID_GROUP_ID = "1af99c2b-bc94-46c7-88a3-229a7cbf3a22"
ADULT_GROUP_ID = "ac3a6dfb-e2be-4a13-a2f9-a81a40305755"
RAM_ID = "640b5aa7-feaa-4905-9d67-f04cdcb180c6"
LANG_ID = "7804f670-67d5-4c10-974e-c2b8f5928f96"
TURBINE_ID = "d96bb139-7e00-40d4-a010-15f28c4f9fca"
TIER_ID = "e9618dec-a1a8-47bd-a8a4-1d823559193d"
HUIS_ID = "827d0b38-dd58-4e2f-8027-11b3bc780a5a"
KROM_ID = "c02ecdad-7819-4d95-819c-879d2aca81bf"
REPAIR_TAG = "birth-gate-repair-2026-10-01"
AREAS = {RAM_ID: "27.96", LANG_ID: "38.32", TURBINE_ID: "84.34", TIER_ID: "108.91"}
BEFORE_KIDS = {RAM_ID: 156, LANG_ID: 0, TURBINE_ID: 0, TIER_ID: 416}
AFTER_KIDS = {RAM_ID: 66, LANG_ID: 90, TURBINE_ID: 182, TIER_ID: 234}
ADULTS = {RAM_ID: 59, LANG_ID: 80, TURBINE_ID: 168, TIER_ID: 218, HUIS_ID: 119, KROM_ID: 61}
BIRTHS = {
    "4d51c4a6-136c-4b53-8e3a-b26d729046b3": 156,
    "c492549b-f5a7-49b2-91fc-8fc155032044": 416,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"Repair precondition failed: {message}")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _validated_state(effective_at: datetime):
    mob = db.session.get(Mob, MOB_ID)
    _require(mob is not None and mob.name == "Lam Bokke" and mob.status == "active"
             and mob.farm_id == FARM_ID, "target mob identity or status changed")
    for group_id, sex, age in [(KID_GROUP_ID, "mixed", "kid"), (ADULT_GROUP_ID, "ewe", "adult")]:
        group = db.session.get(AnimalGroupType, group_id)
        _require(group is not None and (group.species, group.breed, group.sex, group.age_class)
                 == ("Goat", "Angora", sex, age), "target animal type changed")

    sessions = GrazingSession.query.filter_by(mob_id=MOB_ID, end_at=None).all()
    _require(len(sessions) == 1, "expected exactly one active grazing session")
    session = sessions[0]
    _require(set(str(row.paddock_id) for row in session.allocations) == set(ADULTS),
             "occupied paddocks changed; refusing to delete or replace any allocation")
    counts = {
        str(row.paddock_id): {str(item.animal_group_type_id): int(item.head_count)
                              for item in row.group_assignments}
        for row in session.allocations
    }
    expected = {
        paddock_id: {ADULT_GROUP_ID: adult_count, **(
            {KID_GROUP_ID: BEFORE_KIDS[paddock_id]} if BEFORE_KIDS.get(paddock_id) else {}
        )}
        for paddock_id, adult_count in ADULTS.items()
    }
    _require(counts == expected, "current exact kid/adult assignments differ from the verified counts")
    context = AllocationDistributionService.balance_context(mob)
    _require({key: item["head_count"] for key, item in context.items()}
             == {KID_GROUP_ID: 572, ADULT_GROUP_ID: 705}, "stock balances changed")

    births = StockLedgerEntry.query.filter(StockLedgerEntry.id.in_(BIRTHS)).all()
    _require(len(births) == len(BIRTHS) and all(
        row.mob_id == MOB_ID and row.farm_id == FARM_ID and row.animal_group_type_id == KID_GROUP_ID
        and row.event_type == StockEventType.birth and row.quantity == BIRTHS[row.id]
        for row in births
    ), "the two original birth entries changed or are missing")

    for paddock_id, area in AREAS.items():
        paddock = db.session.get(Paddock, paddock_id)
        _require(paddock is not None and paddock.status == "active" and paddock.farm.active
                 and paddock.farm_id == FARM_ID and paddock.area_ha == Decimal(area)
                 and paddock.grazeable_area_ha == Decimal(area), "gate paddock area or status changed")

    calculated = {}
    for anchor, expected_component, total in [
        (RAM_ID, {RAM_ID, LANG_ID}, 156), (TIER_ID, {TIER_ID, TURBINE_ID}, 416)
    ]:
        network = GateService.open_gate_component(db.session.get(Paddock, anchor))
        _require({str(row.id) for row in network} == expected_component,
                 "open gate connections changed")
        distributed = GateService.allocations_for_open_gate_network(FARM_ID, [{
            "paddock_id": anchor,
            "group_counts": [{"animal_group_type_id": KID_GROUP_ID, "head_count": total}],
        }])
        calculated.update({str(row["paddock_id"]): sum(item["head_count"] for item in row["group_counts"])
                           for row in distributed})
    _require(calculated == AFTER_KIDS, "gate weighting no longer produces the agreed correction")

    historical_times = [session.start_at, *(row.event_time for row in births)]
    for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory):
        historical_times.extend(row.effective_from for row in model.query.filter_by(mob_id=MOB_ID).all())
    _require(all(effective_at > _utc(value) for value in historical_times),
             "repair time must be later than existing grazing history and births")
    corrected = {paddock_id: dict(groups) for paddock_id, groups in counts.items()}
    for paddock_id, count in AFTER_KIDS.items():
        corrected[paddock_id][KID_GROUP_ID] = count
    return mob, session, context, corrected


def run_repair(*, apply: bool = False, backup_dir: Path | None = None,
               effective_at: datetime | None = None) -> dict:
    """Run in a fresh application session; commit only the explicitly applied repair."""
    if db.session().in_transaction() or db.session.new or db.session.dirty or db.session.deleted:
        raise ValueError("Run this repair with a fresh database session")
    source_db = sqlite_database_path(current_app)
    _require(source_db is not None and source_db.is_file(),
             "an existing file-backed SQLite database is required")
    effective_at = _utc(effective_at or datetime.now(timezone.utc))
    try:
        if apply:
            _require(db.engine.dialect.name == "sqlite", "apply supports SQLite only")
            # Serialize competing repairs and prevent changes between validation,
            # the backup snapshot, and the allocation update.
            db.session.execute(text("BEGIN IMMEDIATE"))
        previous = MobEvent.query.filter_by(mob_id=MOB_ID, tags_csv=REPAIR_TAG).first()
        if previous is not None:
            result = {"status": "already_applied", "audit_event_id": str(previous.id),
                      "effective_at": _utc(previous.event_at).isoformat()}
            db.session.rollback()
            return result

        mob, session, context, corrected = _validated_state(effective_at)
        result = {
            "status": "applied" if apply else "preview", "mob_id": MOB_ID,
            "effective_at": effective_at.isoformat(), "kid_total": 572, "adult_total": 705,
            "paddocks": [{"id": paddock_id, "name": db.session.get(Paddock, paddock_id).name,
                          "before_kids": BEFORE_KIDS[paddock_id], "after_kids": AFTER_KIDS[paddock_id]}
                         for paddock_id in AFTER_KIDS],
        }
        if not apply:
            db.session.rollback()
            return result

        backup_path = create_backup(source_db=source_db, instance_dir=Path(current_app.instance_path),
                                   backup_dir=Path(backup_dir or current_app.config["BACKUP_DIR"]))
        result["backup"] = str(backup_path.resolve())
        # Revalidate under the same write lock immediately before the mutation.
        mob, session, context, corrected = _validated_state(effective_at)
        allocations = {str(row.paddock_id): row for row in session.allocations}
        payloads = AllocationDistributionService.allocations_from_count_map(mob, corrected, context=context)
        _require({row["paddock_id"] for row in payloads} == set(allocations),
                 "repair must preserve every allocation record")
        for payload in payloads:
            allocation = allocations[payload["paddock_id"]]
            allocation.allocation_fraction = Decimal(payload["allocation_fraction"])
            for group in payload["group_counts"]:
                if group["animal_group_type_id"] != KID_GROUP_ID:
                    continue
                assignment = next((row for row in allocation.group_assignments
                                   if row.animal_group_type_id == KID_GROUP_ID), None)
                if assignment is None:
                    assignment = GrazingAllocationGroupAssignment(animal_group_type_id=KID_GROUP_ID)
                    allocation.group_assignments.append(assignment)
                assignment.head_count = group["head_count"]
                assignment.group_fraction = Decimal(group["group_fraction"])
                assignment.assigned_lsu = Decimal(group["assigned_lsu"])
        db.session.flush()
        GrazingHistoryService.sync_live_history_for_mob(mob, effective_at=effective_at)
        description = (
            "Corrected Lam Bokke kid allocations that did not distribute through already-open gates. "
            "Effective at repair time; original births, balances, cohorts, adults, and earlier history preserved.\n"
            + json.dumps({"birth_entry_ids": list(BIRTHS), **result}, indent=2)
        )
        event = MobEventService.create_event(mob_id=MOB_ID, farm_id=FARM_ID, event_at=effective_at,
                                            raw_tags=REPAIR_TAG, description=description)
        db.session.flush()
        result["audit_event_id"] = str(event.id)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and apply the verified correction.")
    parser.add_argument("--backup-dir", type=Path, help="Override the configured backup directory; never prunes.")
    args = parser.parse_args()
    app = create_app(get_config())
    with app.app_context():
        try:
            result = run_repair(apply=args.apply, backup_dir=args.backup_dir)
        except (ValueError, OSError) as error:
            parser.exit(1, f"{error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
