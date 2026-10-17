from app import create_app
from app.config import get_config
from app.extensions import db
from app.models import Farm, Mob, Paddock
from app.services.farm_capacity_service import FarmCapacityService
from app.services.mob_inventory_service import MobInventoryService


def main() -> None:
    app = create_app(get_config())
    with app.app_context():
        if Farm.query.first():
            print("Data exists; skipping.")
            return

        farm = Farm(name="Script Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Script Mob")
        db.session.add_all(
            [
                Paddock(farm_id=farm.id, name="Script Paddock A", area_ha=8, grazeable_area_ha=7),
                Paddock(farm_id=farm.id, name="Script Paddock B", area_ha=6, grazeable_area_ha=5),
                mob,
            ]
        )
        MobInventoryService.ensure_baseline(mob, source="created")
        FarmCapacityService.record(farm, source="created")
        db.session.commit()
        print("Demo data created")


if __name__ == "__main__":
    main()
