from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Paddock
from app.services.reporting_service import ReportingService

bp = Blueprint("paddocks", __name__)


@bp.get("")
def list_paddocks():
    paddocks = Paddock.query.order_by(Paddock.name).all()
    return jsonify(
        [
            {
                "id": str(p.id),
                "farm_id": p.farm_id,
                "name": p.name,
                "area_ha": float(p.area_ha),
                "grazeable_area_ha": float(p.grazeable_area_ha),
                "status": p.status,
            }
            for p in paddocks
        ]
    )


@bp.post("")
def create_paddock():
    payload = request.get_json() or {}
    required = {"farm_id", "name"}
    if not required.issubset(payload):
        return jsonify({"error": "farm_id and name are required"}), 400

    paddock = Paddock(
        farm_id=payload["farm_id"],
        name=payload["name"],
        area_ha=payload.get("area_ha", 0),
        grazeable_area_ha=payload.get("grazeable_area_ha", 0),
        status=payload.get("status", "active"),
    )
    db.session.add(paddock)
    db.session.commit()
    return jsonify({"id": str(paddock.id), "name": paddock.name}), 201


@bp.get("/<paddock_id>")
def get_paddock(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    return jsonify(
        {
            "id": str(paddock.id),
            "farm_id": paddock.farm_id,
            "name": paddock.name,
            "area_ha": float(paddock.area_ha),
            "grazeable_area_ha": float(paddock.grazeable_area_ha),
            "status": paddock.status,
        }
    )


@bp.patch("/<paddock_id>")
def update_paddock(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    payload = request.get_json() or {}

    for field in ["name", "status", "area_ha", "grazeable_area_ha"]:
        if field in payload:
            setattr(paddock, field, payload[field])

    db.session.commit()
    return jsonify({"id": str(paddock.id), "name": paddock.name})


@bp.get("/<paddock_id>/stock")
def paddock_current_stock(paddock_id):
    Paddock.query.get_or_404(paddock_id)
    totals = ReportingService.paddock_current_stock(paddock_id)
    return jsonify({"paddock_id": paddock_id, "totals": totals})


@bp.get("/<paddock_id>/history")
def paddock_history(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    rows = sorted(
        paddock.grazing_allocations,
        key=lambda a: a.grazing_session.start_at,
        reverse=True,
    )
    return jsonify(
        [
            {
                "session_id": str(a.grazing_session_id),
                "mob_id": a.grazing_session.mob_id,
                "start_at": a.grazing_session.start_at.isoformat(),
                "end_at": a.grazing_session.end_at.isoformat() if a.grazing_session.end_at else None,
                "allocation_fraction": float(a.allocation_fraction),
            }
            for a in rows
        ]
    )
