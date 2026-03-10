from datetime import date

from flask import Flask

from app.extensions import db
from app.models import Farm, Mob, Paddock
from app.services.grazing_history_service import GrazingHistoryService


def init_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    def seed_demo() -> None:
        """Seed a small demo dataset."""
        if Farm.query.first():
            print("Seed skipped: farms already exist")
            return

        farm = Farm(name="Demo Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()

        north = Paddock(farm_id=farm.id, name="North 1", area_ha=12.5, grazeable_area_ha=10.0)
        south = Paddock(farm_id=farm.id, name="South 2", area_ha=9.0, grazeable_area_ha=8.0)
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        db.session.add_all([north, south, mob])
        db.session.commit()

        print(f"Seed complete for {date.today().isoformat()}")

    @app.cli.command("backfill-grazing-lsu-history")
    def backfill_grazing_lsu_history() -> None:
        """Rebuild persisted paddock LSU history from stock ledger and grazing allocations."""
        summary = GrazingHistoryService.backfill_all_from_ledger()
        db.session.commit()
        print(
            "Backfill complete: "
            f"{summary['mobs_backfilled']} mob(s), "
            f"{summary['rows_created']} history row(s)"
        )
