import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy.exc import IntegrityError
from sqlalchemy import MetaData, Table
from sqlalchemy.orm import registry

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm, WaterAsset, WaterAssetStateHistory


REVISION = "d7e9f1a3b5c7"
PARENT = "c6d8e0f213a5"
SPEC = importlib.util.spec_from_file_location(
    "weir_conversion",
    Path(__file__).resolve().parents[2] / "migrations/versions/d7e9f1a3b5c7_split_weir_full_history.py",
)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)
START = datetime(2025, 1, 1)


def timeline(events):
    rows = []
    for index, (seconds, level, active, status) in enumerate(events):
        previous = rows[-1] if rows else {}
        rows.append({
            "id": f"row-{index:03}", "water_asset_id": "weir", "farm_id": "farm",
            "changed_at": START + timedelta(seconds=seconds),
            "change_type": "updated" if rows else "baseline",
            "active": active, "status": status, "water_level": level,
            **{f"previous_{field}": previous.get(field) for field in migration.STATE_FIELDS},
        })
    return rows


@pytest.mark.parametrize("events,cutoff,expected,converted,skipped", [
    ([(0, "full"), (10, "low")], 20, [(0, "overflowing"), (5, "full"), (10, "low")], 1, 0),
    ([(0, "full"), (1, "full"), (10, "low")], 20,
     [(0, "overflowing"), (1, "overflowing"), (5, "full"), (10, "low")], 1, 0),
    ([(0, "full"), (5, "full"), (10, "low")], 20,
     [(0, "overflowing"), (5, "full"), (10, "low")], 1, 0),
    ([(0, "low"), (2, "full")], 10, [(0, "low"), (2, "overflowing"), (6, "full")], 1, 0),
    ([(0, "full"), (4, "low"), (6, "full"), (10, "half")], 20,
     [(0, "overflowing"), (2, "full"), (4, "low"), (6, "overflowing"), (8, "full"), (10, "half")], 2, 0),
    ([(0, "full"), (0, "low")], 10, [(0, "full"), (0, "low")], 0, 1),
    ([(0, "full")], 0, [(0, "full")], 0, 1),
    ([(0, "full"), (0.000001, "low")], 10, [(0, "full"), (0.000001, "low")], 0, 1),
    ([(0, "full"), (0.000003, "low")], 10,
     [(0, "overflowing"), (0.000001, "full"), (0.000003, "low")], 1, 0),
    ([(0, "full"), (5, "full"), (5, "full"), (10, "low")], 20,
     [(0, "overflowing"), (5, "full"), (5, "full"), (10, "low")], 1, 0),
    ([(0, None), (10, "low")], 20, [(0, None), (10, "low")], 0, 0),
    ([], 20, [], 0, 0),
])
def test_split_period_boundaries(events, cutoff, expected, converted, skipped):
    rows = timeline([(seconds, level, True, "operational") for seconds, level in events])
    result, actual_converted, actual_skipped = migration._split_full_periods(
        rows, START + timedelta(seconds=cutoff),
    )
    assert [(row["changed_at"], row["water_level"]) for row in result] == [
        (START + timedelta(seconds=seconds), level) for seconds, level in expected
    ]
    assert (actual_converted, actual_skipped) == (converted, skipped)
    assert [row["water_level"] for row in rows] == [level for _, level in events]
    if result:
        assert result[0]["previous_water_level"] is None
    assert all(b["previous_water_level"] == a["water_level"] for a, b in zip(result, result[1:]))


def test_split_preserves_status_changes_and_synthetic_state():
    rows = timeline([
        (0, "full", True, "operational"),
        (2, "full", False, "damaged"),
        (8, "full", True, "operational"),
        (10, "low", True, "operational"),
    ])
    result, count, _ = migration._split_full_periods(rows, START + timedelta(seconds=20))
    assert count == 1
    midpoint = result[2]
    assert midpoint["changed_at"] == START + timedelta(seconds=5)
    assert midpoint["active"] is midpoint["previous_active"] is False
    assert midpoint["status"] == midpoint["previous_status"] == "damaged"
    originals = {row["id"]: row for row in rows}
    for row in result:
        if row["id"] in originals:
            for field in ("changed_at", "change_type", "active", "status", "previous_active", "previous_status"):
                assert row[field] == originals[row["id"]][field]


def test_aware_times_normalize_to_utc():
    assert migration._utc_naive(datetime(2025, 1, 1, 2, tzinfo=timezone(timedelta(hours=2)))) == START


@pytest.fixture
def migration_app(tmp_path, monkeypatch):
    class MigrationConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "weir.sqlite").as_posix()

    application = create_app(MigrationConfig)
    with application.app_context():
        upgrade(revision=PARENT)
        # Migration tests use the schema at their revision, not today's models.
        mapper = registry()
        metadata = MetaData()
        for model_name, table_name in [("WaterAsset", "water_assets"), ("WaterAssetStateHistory", "water_asset_state_history")]:
            legacy_model = type(model_name, (), {})
            mapper.map_imperatively(legacy_model, Table(table_name, metadata, autoload_with=db.engine))
            legacy_model.query = db.session.query_property()
            monkeypatch.setitem(globals(), model_name, legacy_model)
        yield application
        db.session.remove()
        db.engine.dispose()


def seed_history(asset_type="weir", ongoing=False):
    farm = Farm(name=f"History farm {uuid.uuid4()}")
    db.session.add(farm)
    db.session.flush()
    asset = WaterAsset(id=str(uuid.uuid4()), farm_id=farm.id, name="Asset", asset_type=asset_type, needs_review=False,
                       active=False, status="damaged", water_level="full" if ongoing else "low")
    db.session.add(asset)
    db.session.flush()
    events = [(0, "full", False, "damaged")]
    if not ongoing:
        events.append((10, "low", False, "damaged"))
    for row in timeline(events):
        row.update(id=str(uuid.uuid4()), water_asset_id=asset.id, farm_id=farm.id)
        db.session.add(WaterAssetStateHistory(**row))
    db.session.commit()
    return asset.id


def stored_rows():
    return [dict(row) for row in db.session.execute(db.text(
        "SELECT * FROM water_asset_state_history ORDER BY water_asset_id,changed_at,id"
    )).mappings()]


def test_upgrade_real_database_preserves_assets_and_repeated_upgrade(migration_app):
    weir_id = seed_history()
    dam_id = seed_history("ground_dam")
    ongoing_id = seed_history(ongoing=True)
    originals = stored_rows()
    db.session.remove()
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    upgrade(revision=REVISION)
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    revised = stored_rows()
    assert len(revised) == len(originals) + 2
    assert [row for row in revised if row["water_asset_id"] == dam_id] == [
        row for row in originals if row["water_asset_id"] == dam_id
    ]
    midpoint = WaterAssetStateHistory.query.filter_by(water_asset_id=ongoing_id, water_level="full").one()
    assert START + (before - START) // 2 <= midpoint.changed_at <= START + (after - START) // 2
    assert db.session.get(WaterAsset, weir_id).water_level == "low"
    assert db.session.get(WaterAsset, ongoing_id).water_level == "full"
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
    db.session.remove()
    upgrade(revision=REVISION)
    assert stored_rows() == revised
    db.session.remove()
    with pytest.raises(RuntimeError, match="Restore the pre-migration"):
        migration.downgrade()
    with pytest.raises(SystemExit, match="1"):
        downgrade(revision=PARENT)
    assert stored_rows() == revised
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == REVISION


@pytest.mark.parametrize("failure", ["current", "previous", "future", "write"])
def test_migration_failure_is_atomic(migration_app, failure):
    seed_history()
    asset_id = seed_history()
    if failure == "current":
        db.session.get(WaterAsset, asset_id).water_level = "half"
    elif failure == "previous":
        WaterAssetStateHistory.query.filter_by(water_asset_id=asset_id, water_level="low").one().previous_water_level = "empty"
    elif failure == "future":
        WaterAssetStateHistory.query.filter_by(water_asset_id=asset_id, water_level="low").one().changed_at = datetime.now() + timedelta(days=10)
    else:
        db.session.execute(db.text(
            "CREATE TRIGGER fail_midpoint BEFORE INSERT ON water_asset_state_history "
            "BEGIN SELECT RAISE(ABORT, 'test insertion failure'); END"
        ))
    db.session.commit()
    originals = stored_rows()
    db.session.remove()
    # Flask-Migrate converts migration RuntimeErrors to a nonzero CLI exit.
    with pytest.raises(IntegrityError if failure == "write" else SystemExit):
        upgrade(revision=REVISION)
    assert stored_rows() == originals
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == PARENT


def test_empty_database_upgrade(migration_app):
    upgrade(revision=REVISION)
    assert stored_rows() == []
