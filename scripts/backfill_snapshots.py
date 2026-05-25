from datetime import date

from app import create_app
from app.config import get_config
from app.extensions import db
from app.models import AnimalGroupBalance, DailyStockSnapshot, Farm, Paddock
from app.services.reporting_service import ReportingService


def main() -> None:
    app = create_app(get_config())
    with app.app_context():
        snapshot_date = date.today()
        farms = Farm.query.all()

        for farm in farms:
            for paddock in Paddock.query.filter_by(farm_id=farm.id).all():
                stock = ReportingService.paddock_current_stock(paddock.id)
                for group_id, total in stock.items():
                    db.session.merge(
                        DailyStockSnapshot(
                            farm_id=farm.id,
                            snapshot_date=snapshot_date,
                            paddock_id=paddock.id,
                            animal_group_type_id=group_id,
                            head_count=total,
                        )
                    )

        db.session.commit()
        print(f"Snapshots backfilled for {snapshot_date.isoformat()}")


if __name__ == "__main__":
    main()
