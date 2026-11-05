from types import SimpleNamespace

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import MetaData, Table
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm
from app.services.weir_volume_service import estimate

PARENT = "a7c3e9f1b502"
REVISION = "b8d4f0a2c613"


@pytest.fixture
def capacity_database(tmp_path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "capacity.sqlite").as_posix()

    app = create_app(Config)
    with app.app_context():
        upgrade(revision=PARENT)
        farm = Farm(name="Capacity farm")
        db.session.add(farm)
        db.session.flush()
        polygon = {"type": "Polygon", "coordinates": [[[0, 0], [.001, 0], [.001, .0001], [0, .0001], [0, 0]]]}
        line = {"type": "LineString", "coordinates": [[0, .00005], [.001, .00005]]}
        # Seed the schema at this revision, independent of the current ORM.
        assets = Table("water_assets", MetaData(), autoload_with=db.engine)
        history = Table("water_asset_state_history", MetaData(), autoload_with=db.engine)
        for name, kind in (("polygon", "weir"), ("review", "weir"), ("point", "weir"), ("tank", "tank")):
            geometry = dict(weir_polygon=polygon, weir_centerline=line, weir_wall_height_m=1) if name in {"polygon", "review"} else {}
            db.session.execute(assets.insert().values(id=name, farm_id=farm.id, name=name, asset_type=kind,
                active=True, needs_review=False, capacity_m3=123, weir_geometry_review_required=name == "review", **geometry))
            db.session.execute(history.insert().values(id=name, farm_id=farm.id, water_asset_id=name,
                change_type="created", active=True))
        db.session.commit()
        yield
        db.session.remove()
        db.engine.dispose()


def snapshot():
    return {table: [dict(row) for row in db.session.execute(db.text(f"SELECT * FROM {table} ORDER BY id")).mappings()]
            for table in ("water_assets", "water_asset_state_history")}


def test_capacity_backfill_preserves_other_data_and_repeats_safely(capacity_database):
    before = snapshot()
    db.session.remove()
    upgrade(revision=REVISION)
    after = snapshot()
    assert after["water_asset_state_history"] == before["water_asset_state_history"]
    for old, new in zip(before["water_assets"], after["water_assets"]):
        assert {k: v for k, v in new.items() if k != "capacity_m3"} == {k: v for k, v in old.items() if k != "capacity_m3"}
        if old["name"] == "polygon":
            assets = Table("water_assets", MetaData(), autoload_with=db.engine)
            asset = db.session.execute(assets.select().where(assets.c.id == "polygon")).mappings().one()
            assert float(asset["capacity_m3"]) == estimate(SimpleNamespace(**asset))["capacity_m3"]
            assert new["capacity_m3"] != 123
        else:
            assert new["capacity_m3"] == (None if old["name"] == "review" else 123)
    db.session.remove()
    upgrade(revision=REVISION)
    assert snapshot() == after
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []


def test_capacity_backfill_failure_is_atomic(capacity_database):
    db.session.execute(db.text("CREATE TRIGGER reject_capacity BEFORE UPDATE ON water_assets BEGIN SELECT RAISE(ABORT, 'capacity failure'); END"))
    db.session.commit()
    before = snapshot()
    db.session.remove()
    with pytest.raises(IntegrityError, match="capacity failure"):
        upgrade(revision=REVISION)
    assert snapshot() == before
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == PARENT


def test_underflow_upgrade_preserves_readings_and_protects_downgrade(capacity_database):
    upgrade(revision=REVISION)
    db.session.execute(db.text("UPDATE water_assets SET is_overflowing=1, water_level_percent=100 WHERE id='point'"))
    db.session.execute(db.text("UPDATE water_asset_state_history SET previous_is_overflowing=0 WHERE id='point'"))
    db.session.commit()
    before = snapshot()
    db.session.remove()
    underflow_revision = "c9e5a1b3d724"
    upgrade(revision=underflow_revision)
    after = snapshot()
    for table, old_rows in before.items():
        for old, new in zip(old_rows, after[table], strict=True):
            assert {key: new[key] for key in old} == old
            assert new["is_underflowing"] == 0
            if table == "water_asset_state_history":
                assert new["previous_is_underflowing"] == (0 if old["previous_is_overflowing"] is not None else None)
    db.session.remove()
    downgrade(revision=REVISION)
    assert snapshot() == before
    db.session.remove()
    upgrade(revision=underflow_revision)
    for name in ("point", "tank", "polygon"):
        with pytest.raises(IntegrityError):
            db.session.execute(db.text("UPDATE water_assets SET is_underflowing=1 WHERE id=:name"), {"name": name})
        db.session.rollback()
    db.session.execute(db.text("UPDATE water_asset_state_history SET is_underflowing=1, status='damaged' WHERE id='point'"))
    db.session.commit()
    with pytest.raises(SystemExit):
        downgrade(revision=REVISION)
    assert db.session.execute(db.text("SELECT version_num FROM alembic_version")).scalar() == underflow_revision
    assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []
