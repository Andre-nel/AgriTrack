import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocationGroupAssignment,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    MobEvent,
    Paddock,
    PaddockGate,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.allocation_distribution_service import AllocationDistributionService
from app.services.grazing_history_service import GrazingHistoryService
from scripts import repair_birth_gate_allocations as repair

START = datetime(2026, 10, 1, 11, tzinfo=timezone.utc)
REPAIR_AT = START + timedelta(hours=1)


@pytest.fixture
def repair_app(tmp_path):
    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'repair.db').as_posix()}"
        BACKUP_DIR = str(tmp_path / "backups")

    app = create_app(FileConfig)
    with app.app_context():
        db.create_all()
        farm = Farm(id=repair.FARM_ID, name="Repair Farm", timezone="SAST", active=True)
        kid = AnimalGroupType(id=repair.KID_GROUP_ID, species="Goat", breed="Angora", sex="mixed", age_class="kid")
        adult = AnimalGroupType(id=repair.ADULT_GROUP_ID, species="Goat", breed="Angora", sex="ewe", age_class="adult")
        mob = Mob(id=repair.MOB_ID, farm_id=farm.id, name="Lam Bokke", status="active")
        db.session.add_all([farm, kid, adult, mob])
        db.session.flush()
        names = dict(zip(repair.ADULTS, ["Ram Kampie", "Lang Gaat", "Turbine", "Tier Kloofie", "Huis Kamp", "Krom Leegte"]))
        for paddock_id in repair.ADULTS:
            db.session.add(Paddock(id=paddock_id, farm_id=farm.id, name=names[paddock_id],
                                   area_ha=repair.AREAS.get(paddock_id, "10"),
                                   grazeable_area_ha=repair.AREAS.get(paddock_id, "10")))
        for left, right in [(repair.RAM_ID, repair.LANG_ID), (repair.TIER_ID, repair.TURBINE_ID)]:
            left, right = sorted([left, right])
            db.session.add(PaddockGate(farm_id=farm.id, paddock_a_id=left, paddock_b_id=right,
                                      active=True, status="open"))
        adult_cohort = AnimalCohort(animal_group_type_id=adult.id, origin="legacy", origin_farm_id=farm.id)
        db.session.add(adult_cohort)
        db.session.flush()
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=adult.id,
                                         cohort_id=adult_cohort.id, head_count=705))
        for birth_id, quantity in repair.BIRTHS.items():
            cohort = AnimalCohort(animal_group_type_id=kid.id, origin="birth", origin_farm_id=farm.id)
            db.session.add(cohort)
            db.session.flush()
            db.session.add_all([
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=kid.id,
                                   cohort_id=cohort.id, head_count=quantity),
                StockLedgerEntry(id=birth_id, farm_id=farm.id, mob_id=mob.id,
                                 animal_group_type_id=kid.id, cohort_id=cohort.id,
                                 event_type=StockEventType.birth, quantity=quantity,
                                 event_time=START - timedelta(minutes=1)),
            ])
        db.session.flush()
        session = GrazingSession(mob_id=mob.id, farm_id=farm.id, start_at=START)
        db.session.add(session)
        db.session.flush()
        counts = {
            paddock_id: {adult.id: adult_count, **(
                {kid.id: repair.BEFORE_KIDS[paddock_id]} if repair.BEFORE_KIDS.get(paddock_id) else {}
            )}
            for paddock_id, adult_count in repair.ADULTS.items()
        }
        AllocationDistributionService.rewrite_session_assignments(session, counts)
        GrazingHistoryService.sync_live_history_for_mob(mob, effective_at=START)
        db.session.commit()
        db.session.remove()
        yield app
        db.session.remove()
        db.engine.dispose()


def _rows(model, *criteria):
    return [dict(row) for row in db.session.execute(
        db.select(model.__table__).where(*criteria).order_by(model.id)
    ).mappings()]


def _counts():
    session = GrazingSession.query.filter_by(mob_id=repair.MOB_ID, end_at=None).one()
    return {str(row.paddock_id): {str(item.animal_group_type_id): item.head_count
                                for item in row.group_assignments}
            for row in session.allocations}


def test_preview_is_read_only_and_reports_exact_correction(repair_app, tmp_path):
    before = (tmp_path / "repair.db").read_bytes()
    result = repair.run_repair(effective_at=REPAIR_AT)
    assert result["status"] == "preview"
    assert {row["id"]: row["after_kids"] for row in result["paddocks"]} == repair.AFTER_KIDS
    assert {row["id"]: row["before_kids"] for row in result["paddocks"]} == repair.BEFORE_KIDS
    assert result["kid_total"] == 572 and result["adult_total"] == 705
    assert (tmp_path / "repair.db").read_bytes() == before
    assert not (tmp_path / "backups").exists()
    assert MobEvent.query.count() == 0


def test_apply_preserves_stock_adults_sessions_and_earlier_history(repair_app, tmp_path):
    preserved = {model: _rows(model) for model in (AnimalCohort, AnimalGroupBalance, StockLedgerEntry, GrazingSession)}
    adult_rows = _rows(GrazingAllocationGroupAssignment,
                      GrazingAllocationGroupAssignment.animal_group_type_id == repair.ADULT_GROUP_ID)
    session = GrazingSession.query.filter_by(mob_id=repair.MOB_ID, end_at=None).one()
    allocation_ids = {row.id for row in session.allocations}
    history = {model: _rows(model) for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory)}
    db.session.rollback()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    sentinel = backup_dir / "agritrack-backup-older.zip"
    sentinel.write_bytes(b"keep this older backup")

    result = repair.run_repair(apply=True, effective_at=REPAIR_AT)

    assert result["status"] == "applied"
    assert sentinel.read_bytes() == b"keep this older backup"
    with zipfile.ZipFile(result["backup"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert "agritrack.db" in manifest["includes"]
        restored = tmp_path / "backup.db"
        restored.write_bytes(archive.read("agritrack.db"))
    with sqlite3.connect(restored) as snapshot:
        assert snapshot.execute("select sum(head_count) from grazing_allocation_group_assignments where animal_group_type_id = ?",
                                (repair.KID_GROUP_ID,)).fetchone() == (572,)
        assert snapshot.execute("select count(*) from mob_events").fetchone() == (0,)
        assert snapshot.execute("select count(*) from grazing_allocation_group_assignments where animal_group_type_id = ?",
                                (repair.KID_GROUP_ID,)).fetchone() == (2,)
    counts = _counts()
    assert {key: groups.get(repair.KID_GROUP_ID, 0) for key, groups in counts.items() if key in repair.AFTER_KIDS} == repair.AFTER_KIDS
    assert {key: groups[repair.ADULT_GROUP_ID] for key, groups in counts.items()} == repair.ADULTS
    for model, before in preserved.items():
        assert _rows(model) == before
    assert _rows(GrazingAllocationGroupAssignment,
                 GrazingAllocationGroupAssignment.animal_group_type_id == repair.ADULT_GROUP_ID) == adult_rows
    session = GrazingSession.query.filter_by(mob_id=repair.MOB_ID, end_at=None).one()
    assert {row.id for row in session.allocations} == allocation_ids
    for model, before_rows in history.items():
        for before in before_rows:
            current = db.session.get(model, before["id"])
            for key, value in before.items():
                if key not in {"effective_to", "updated_at"}:
                    assert getattr(current, key) == value
            assert current.effective_to in {None, REPAIR_AT.replace(tzinfo=None)}
    live_kids = GrazingAllocationLsuBreakdownHistory.query.filter_by(
        mob_id=repair.MOB_ID, animal_group_type_id=repair.KID_GROUP_ID, effective_to=None
    ).all()
    assert {row.paddock_id: int(row.allocated_head_count) for row in live_kids} == repair.AFTER_KIDS
    assert all(row.effective_from == REPAIR_AT.replace(tzinfo=None) for row in live_kids)
    event = MobEvent.query.one()
    assert event.tags_csv == repair.REPAIR_TAG
    assert all(birth_id in event.description for birth_id in repair.BIRTHS)
    assert '"before_kids": 156' in event.description
    assert '"after_kids": 66' in event.description


@pytest.mark.parametrize("change", ["counts", "adults", "area", "gate", "extra_gate", "zero_area_gate", "birth", "balance"])
def test_repair_refuses_changed_preconditions_before_backup(repair_app, tmp_path, change):
    if change in {"counts", "adults"}:
        group_id = repair.KID_GROUP_ID if change == "counts" else repair.ADULT_GROUP_ID
        GrazingAllocationGroupAssignment.query.filter_by(animal_group_type_id=group_id).first().head_count += 1
    elif change == "area":
        db.session.get(Paddock, repair.RAM_ID).grazeable_area_ha = 28
    elif change == "gate":
        PaddockGate.query.first().status = "closed"
    elif change in {"extra_gate", "zero_area_gate"}:
        if change == "zero_area_gate":
            paddock = db.session.get(Paddock, repair.HUIS_ID)
            paddock.area_ha = 0
            paddock.grazeable_area_ha = 0
        left, right = sorted([repair.RAM_ID, repair.HUIS_ID])
        db.session.add(PaddockGate(farm_id=repair.FARM_ID, paddock_a_id=left, paddock_b_id=right,
                                  active=True, status="open"))
    elif change == "birth":
        db.session.get(StockLedgerEntry, next(iter(repair.BIRTHS))).quantity += 1
    elif change == "balance":
        AnimalGroupBalance.query.filter_by(animal_group_type_id=repair.KID_GROUP_ID).first().head_count += 1
    db.session.commit()
    db.session.remove()
    before = (tmp_path / "repair.db").read_bytes()
    with pytest.raises(ValueError, match="Repair precondition failed"):
        repair.run_repair(apply=True, effective_at=REPAIR_AT)
    assert (tmp_path / "repair.db").read_bytes() == before
    assert not (tmp_path / "backups").exists()
    assert MobEvent.query.count() == 0


def test_repair_rolls_back_all_changes_when_history_update_fails(repair_app, monkeypatch):
    before = _counts()
    assignments = _rows(GrazingAllocationGroupAssignment)
    history = _rows(GrazingAllocationLsuBreakdownHistory)
    db.session.rollback()

    def fail_history(*args, **kwargs):
        raise RuntimeError("simulated history failure")

    monkeypatch.setattr(GrazingHistoryService, "sync_live_history_for_mob", fail_history)
    with pytest.raises(RuntimeError, match="simulated history failure"):
        repair.run_repair(apply=True, effective_at=REPAIR_AT)
    assert _counts() == before
    assert _rows(GrazingAllocationGroupAssignment) == assignments
    assert _rows(GrazingAllocationLsuBreakdownHistory) == history
    assert MobEvent.query.count() == 0


def test_backup_failure_stops_repair_before_mutation(repair_app, monkeypatch):
    before = _counts()
    db.session.rollback()

    def fail_backup(**kwargs):
        raise OSError("backup disk unavailable")

    monkeypatch.setattr(repair, "create_backup", fail_backup)
    with pytest.raises(OSError, match="backup disk unavailable"):
        repair.run_repair(apply=True, effective_at=REPAIR_AT)
    assert _counts() == before
    assert MobEvent.query.count() == 0


def test_repair_holds_write_lock_during_backup_and_rechecks_state(repair_app, tmp_path, monkeypatch):
    original_backup = repair.create_backup

    def backup_and_change(**kwargs):
        with sqlite3.connect(tmp_path / "repair.db", timeout=0) as competing_connection:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing_connection.execute("update mobs set name='Changed' where id=?", (repair.MOB_ID,))
        backup = original_backup(**kwargs)
        db.session.get(Paddock, repair.RAM_ID).grazeable_area_ha = 99
        return backup

    monkeypatch.setattr(repair, "create_backup", backup_and_change)
    with pytest.raises(ValueError, match="area or status changed"):
        repair.run_repair(apply=True, effective_at=REPAIR_AT)
    assert db.session.get(Paddock, repair.RAM_ID).grazeable_area_ha == Decimal("27.96")
    assert MobEvent.query.count() == 0


def test_repeat_run_is_noop_without_new_backup_or_audit(repair_app, tmp_path):
    first = repair.run_repair(apply=True, effective_at=REPAIR_AT)
    before = (tmp_path / "repair.db").read_bytes()
    backups = list((tmp_path / "backups").iterdir())
    second = repair.run_repair(apply=True, effective_at=REPAIR_AT + timedelta(hours=1))
    assert second["status"] == "already_applied"
    assert second["audit_event_id"] == first["audit_event_id"]
    assert second["effective_at"] == first["effective_at"]
    assert (tmp_path / "repair.db").read_bytes() == before
    assert list((tmp_path / "backups").iterdir()) == backups
    assert MobEvent.query.count() == 1


def test_repair_rejects_time_that_would_rewrite_existing_history(repair_app):
    with pytest.raises(ValueError, match="repair time must be later"):
        repair.run_repair(apply=True, effective_at=START)


def test_preview_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing.db"

    class MissingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{missing.as_posix()}"

    app = create_app(MissingConfig)
    with app.app_context():
        with pytest.raises(ValueError, match="existing file-backed SQLite database"):
            repair.run_repair()
        db.session.remove()
        db.engine.dispose()
    assert not missing.exists()
