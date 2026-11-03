from datetime import datetime, timedelta

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db

PARENT = "f9b2d4e6a810"
REVISION = "a7c3e9f1b502"
LEVELS = [None, "empty", "low", "half", "high", "full", "overflowing", None]


@pytest.fixture
def migration_app(tmp_path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "percent.sqlite").as_posix()
    app = create_app(Config)
    with app.app_context():
        upgrade(revision=PARENT)
        db.session.execute(db.text("INSERT INTO farms (id,name,timezone,active,default_stocking_rate_ha_per_lsu) VALUES ('farm','Migration farm','SAST',1,10)"))
        for asset_id, kind in [("weir", "weir"), ("dam", "ground_dam")]:
            db.session.execute(db.text("INSERT INTO water_assets (id,farm_id,name,asset_type,active,needs_review,water_level) VALUES (:id,'farm',:id,:kind,1,0,NULL)"), {"id": asset_id, "kind": kind})
            previous = None
            for i, value in enumerate(LEVELS):
                db.session.execute(db.text("INSERT INTO water_asset_state_history (id,water_asset_id,farm_id,change_type,changed_at,active,water_level,previous_water_level) VALUES (:id,:asset,'farm',:change,:at,1,:level,:previous)"),
                                   {"id": f"{asset_id}-{i}", "asset": asset_id, "change": "baseline" if i == 0 else "updated", "at": datetime(2026, 1, 1) + timedelta(days=i), "level": value, "previous": previous})
                previous = value
        db.session.commit()
        yield app
        db.session.remove()
        db.engine.dispose()


def rows(table):
    return [dict(row) for row in db.session.execute(db.text(f"SELECT * FROM {table} ORDER BY id")).mappings()]


def test_percentage_migration_preserves_history_and_non_weirs(migration_app):
    for index, level in enumerate([*LEVELS, "", "unknown"]):
        db.session.execute(db.text("INSERT INTO water_assets (id,farm_id,name,asset_type,active,needs_review,water_level) VALUES (:id,'farm',:id,'weir',1,0,:level)"), {"id": f"current-{index}", "level": level})
    db.session.commit()
    original = rows("water_asset_state_history")
    db.session.remove()
    upgrade(revision=REVISION)
    after = rows("water_asset_state_history")
    for index, percent in enumerate([None, 0, 25, 50, 75, 100, 100, None, None, None]):
        row = db.session.execute(db.text("SELECT * FROM water_assets WHERE id=:id"), {"id": f"current-{index}"}).mappings().one()
        assert row["water_level"] is None and row["water_level_percent"] == percent
        assert bool(row["is_overflowing"]) == (index == 6)
    expected = [None, 0, 25, 50, 75, 100, 100, None]
    for old, new in zip(original, after):
        preserved = {key: new[key] for key in old if key not in {"water_level", "previous_water_level"}}
        assert preserved == {key: old[key] for key in preserved}
        if old["water_asset_id"] == "dam":
            assert all(new[key] == value for key, value in old.items())
        else:
            index = int(old["id"].split("-")[-1])
            assert new["water_level"] is new["previous_water_level"] is None
            assert new["water_level_percent"] == expected[index]
            assert bool(new["is_overflowing"]) == (index == 6)
            assert new["previous_water_level_percent"] == (expected[index - 1] if index else None)
            assert bool(new["previous_is_overflowing"]) == (index == 7)
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
    assert db.session.execute(db.text("PRAGMA integrity_check")).scalar() == "ok"
    db.session.remove()
    upgrade(revision=REVISION)
    assert rows("water_asset_state_history") == after
    db.session.remove()
    with pytest.raises(SystemExit):
        downgrade(revision=PARENT)
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == REVISION


@pytest.mark.parametrize("failure", ["invalid_current", "invalid_previous", "write"])
def test_migration_failure_rolls_back_schema_and_data(migration_app, failure):
    if failure == "invalid_current":
        db.session.execute(db.text("UPDATE water_assets SET water_level='unexpected' WHERE id='weir'"))
    elif failure == "invalid_previous":
        db.session.execute(db.text("UPDATE water_asset_state_history SET previous_water_level='unexpected' WHERE id='weir-3'"))
    else:
        db.session.execute(db.text("CREATE TRIGGER reject_conversion BEFORE UPDATE ON water_asset_state_history BEGIN SELECT RAISE(ABORT, 'test conversion failure'); END"))
    db.session.commit()
    before = rows("water_asset_state_history")
    db.session.remove()
    with pytest.raises(IntegrityError if failure == "write" else SystemExit):
        upgrade(revision=REVISION)
    assert rows("water_asset_state_history") == before
    assert "water_level_percent" not in {c["name"] for c in inspect(db.engine).get_columns("water_assets")}
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == PARENT
