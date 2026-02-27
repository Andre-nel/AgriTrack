from app import create_app
from app.config import get_config
from app.extensions import db
from app.models import Farm, Mob, Paddock


def main() -> None:
    app = create_app(get_config())
    with app.app_context():
        db.create_all()
        if Farm.query.first():
            print("Data exists; skipping.")
            return

        farm = Farm(name="Script Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()

        db.session.add_all(
            [
                Paddock(farm_id=farm.id, name="Script Paddock A", area_ha=8, grazeable_area_ha=7),
                Paddock(farm_id=farm.id, name="Script Paddock B", area_ha=6, grazeable_area_ha=5),
                Mob(farm_id=farm.id, name="Script Mob"),
            ]
        )
        db.session.commit()
        print("Demo data created")


if __name__ == "__main__":
    main()
