import pytest
from flask_migrate import upgrade
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm, WaterAsset
from app.services.water_network_service import WaterNetworkService as Water

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
        for name, kind in (("polygon", "weir"), ("review", "weir"), ("point", "weir"), ("tank", "tank")):
            geometry = dict(weir_polygon=polygon, weir_centerline=line) if name in {"polygon", "review"} else {}
            asset = Water.create_asset(dict(farm_id=farm.id, name=name, asset_type=kind, capacity_m3=123, **geometry))
            # Reproduce the previously deployed separate manual-capacity state.
            asset.capacity_m3 = 123
            asset.weir_geometry_review_required = name == "review"
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
            asset = WaterAsset.query.filter_by(name="polygon").one()
            assert float(asset.capacity_m3) == Water.serialize_asset(asset)["capacity_m3"]
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
