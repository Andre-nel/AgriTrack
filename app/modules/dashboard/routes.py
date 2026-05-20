from flask import render_template

from app.models import Farm
from app.services.reporting_service import ReportingService


def register_legacy_routes(bp) -> None:
    @bp.get("/")
    def dashboard():
        summary = ReportingService.dashboard_summary()
        farms = Farm.query.order_by(Farm.name).all()

        farm_cards = []
        for farm in farms:
            paddocks = len([paddock for paddock in farm.paddocks if paddock.status == "active"])
            mobs = len([mob for mob in farm.mobs if mob.status == "active"])
            ready = paddocks > 0 and mobs > 0
            farm_cards.append(
                {
                    "id": str(farm.id),
                    "name": farm.name,
                    "timezone": farm.timezone,
                    "paddock_count": paddocks,
                    "mob_count": mobs,
                    "rainfall_count": len(farm.rainfall_records),
                    "ready": ready,
                }
            )

        return render_template("dashboard.html", summary=summary, farm_cards=farm_cards)

