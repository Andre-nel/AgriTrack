from flask import jsonify, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, Paddock
from app.services.gate_service import GateService
from app.services.reporting_service import ReportingService


def _dashboard_gate_farm_options() -> list[dict]:
    farms = Farm.query.filter_by(active=True).order_by(Farm.name.asc()).all()
    return [
        {"id": str(farm.id), "name": farm.name}
        for farm in farms
        if any(paddock.status == "active" for paddock in farm.paddocks)
    ]


def _dashboard_gate_paddock_options() -> list[dict]:
    paddocks = (
        Paddock.query.join(Farm)
        .filter(Paddock.status == "active", Farm.active.is_(True))
        .order_by(Farm.name.asc(), Paddock.name.asc())
        .all()
    )
    return [
        {
            "id": str(paddock.id),
            "name": paddock.name,
            "farm_id": str(paddock.farm_id),
            "farm_name": paddock.farm.name if paddock.farm else "",
            "label": f"{paddock.farm.name}: {paddock.name}" if paddock.farm else paddock.name,
        }
        for paddock in paddocks
    ]


def _dashboard_gate_payload(gate) -> dict:
    row = GateService.serialize_gate(gate)
    farm_id = row["farm_id"]
    gate_id = row["id"]
    row["gate_detail_url"] = url_for("web.farm_gate_detail", farm_id=farm_id, gate_id=gate_id)
    row["gate_state_url"] = url_for("web.update_farm_gate_state_form", farm_id=farm_id, gate_id=gate_id)
    row["gate_location_url"] = url_for("web.update_farm_gate_location", farm_id=farm_id, gate_id=gate_id)
    return row


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

        return render_template(
            "dashboard.html",
            summary=summary,
            farm_cards=farm_cards,
            gate_farm_options=_dashboard_gate_farm_options(),
            gate_paddock_options=_dashboard_gate_paddock_options(),
        )

    @bp.post("/dashboard/gates")
    def create_dashboard_gate_form():
        payload = request.get_json(silent=True) or request.form
        try:
            gate = GateService.create_manual_gate(
                farm_id=payload.get("farm_id"),
                paddock_a_id=payload.get("paddock_a_id"),
                paddock_b_id=payload.get("paddock_b_id"),
                latitude=(payload.get("latitude") or None),
                longitude=(payload.get("longitude") or None),
            )
            db.session.commit()
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            return jsonify({"error": str(exc)}), 400
        return jsonify({"gate": _dashboard_gate_payload(gate)})
