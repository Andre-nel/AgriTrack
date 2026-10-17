from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from flask_migrate import downgrade, upgrade

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import (
    AnimalGroupBalance, AnimalGroupType, Farm, FarmCapacityHistory,
    Mob, MobInventoryBaselineGroup, MobInventoryStateHistory, Paddock, StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType


@pytest.mark.parametrize("populated", [False, True])
def test_lsu_migration_seeds_observed_baselines_and_roundtrips(tmp_path, populated):
    class MigrationConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "migration.sqlite").as_posix()

    application = create_app(MigrationConfig)
    with application.app_context():
        upgrade(revision="8f9a0b1c2d3e")
        if populated:
            farm = Farm(name="Migration farm", default_stocking_rate_ha_per_lsu=6)
            empty_farm = Farm(name="Empty migration farm")
            precision_farm = Farm(name="Precision migration farm", default_stocking_rate_ha_per_lsu=6.126)
            db.session.add_all([farm, empty_farm, precision_farm])
            db.session.flush()
            mob = Mob(farm_id=farm.id, name="Legacy counted mob")
            empty_mob = Mob(farm_id=empty_farm.id, name="Empty mob")
            group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
            db.session.add_all([mob, empty_mob, group])
            db.session.add_all([
                Paddock(farm_id=farm.id, name="Fallback", area_ha=60, grazeable_area_ha=0),
                Paddock(farm_id=farm.id, name="Override", area_ha=60, grazeable_area_ha=30,
                        stocking_rate_ha_per_lsu_override=3),
                Paddock(farm_id=farm.id, name="Retired", area_ha=600, status="inactive"),
                Paddock(farm_id=precision_farm.id, name="Rounded inputs", area_ha=12.346),
            ])
            db.session.flush()
            db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=12))
            for quantity, event_time in [(7, datetime(2025, 1, 1, tzinfo=timezone.utc)),
                                         (3, datetime.now(timezone.utc) + timedelta(days=30))]:
                db.session.add(StockLedgerEntry(
                    farm_id=farm.id, mob_id=mob.id, animal_group_type_id=group.id,
                    event_type=StockEventType.purchase, quantity=quantity, event_time=event_time,
                ))
            db.session.commit()
            farm_id, mob_id = farm.id, mob.id
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.remove()
        upgrade(revision="fa2b4c6d8e10")
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        if populated:
            capacities = FarmCapacityHistory.query.all()
            assert len(capacities) == 3
            assert {row.capacity_lsu for row in capacities} == {
                Decimal("20"), Decimal("0"),
                (Decimal("12.35") / Decimal("6.13")).quantize(Decimal("0.000001")),
            }
            assert all(before <= row.effective_at <= after for row in capacities)
            assert all(row.source == "baseline" for row in capacities)
            states = MobInventoryStateHistory.query.all()
            assert len(states) == 2  # Empty inventory has an observed anchor too.
            assert {row.effective_at for row in states} == {capacities[0].effective_at}
            baseline = MobInventoryStateHistory.query.filter_by(mob_id=mob_id).one()
            assert baseline.farm_id == farm_id
            detail = baseline.baseline_groups[0]
            assert detail.head_count == 12
            assert detail.ledger_net == 10  # Includes the pre-existing future entry.
        else:
            assert FarmCapacityHistory.query.count() == 0
            assert MobInventoryStateHistory.query.count() == 0
        assert db.session.execute(db.text("PRAGMA foreign_key_check")).all() == []

        db.session.remove()
        downgrade(revision="8f9a0b1c2d3e")
        assert Farm.query.count() == (3 if populated else 0)
        db.session.remove()
        upgrade(revision="fa2b4c6d8e10")
        assert MobInventoryBaselineGroup.query.count() == (1 if populated else 0)
        db.session.remove()
        db.engine.dispose()
