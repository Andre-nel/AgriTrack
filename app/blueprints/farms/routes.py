from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Farm, RainfallRecord

bp = Blueprint("farms", __name__)


@bp.get("")
def list_farms():
    farms = Farm.query.order_by(Farm.name).all()
    return jsonify(
        [
            {"id": str(f.id), "name": f.name, "timezone": f.timezone, "active": f.active}
            for f in farms
        ]
    )


@bp.post("")
def create_farm():
    payload = request.get_json() or {}
    if "name" not in payload:
        return jsonify({"error": "name is required"}), 400

    farm = Farm(name=payload["name"], timezone=payload.get("timezone", "UTC"), active=True)
    db.session.add(farm)
    db.session.commit()
    return jsonify({"id": str(farm.id), "name": farm.name}), 201


@bp.get("/<farm_id>")
def get_farm(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    return jsonify({"id": str(farm.id), "name": farm.name, "timezone": farm.timezone, "active": farm.active})


@bp.patch("/<farm_id>")
def update_farm(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    payload = request.get_json() or {}

    farm.name = payload.get("name", farm.name)
    farm.timezone = payload.get("timezone", farm.timezone)
    if "active" in payload:
        farm.active = bool(payload["active"])

    db.session.commit()
    return jsonify({"id": str(farm.id), "name": farm.name})


@bp.get("/<farm_id>/stock")
def farm_stock(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    totals = {}
    for mob in farm.mobs:
        for bal in mob.balances:
            key = bal.animal_group_type_id
            totals[key] = totals.get(key, 0) + bal.head_count

    return jsonify({"farm_id": str(farm.id), "totals": totals})


@bp.get("/<farm_id>/rainfall")
def list_rainfall(farm_id):
    Farm.query.get_or_404(farm_id)
    rows = (
        RainfallRecord.query.filter_by(farm_id=farm_id)
        .order_by(RainfallRecord.recorded_on.desc())
        .limit(120)
        .all()
    )
    return jsonify(
        [
            {
                "id": str(r.id),
                "recorded_on": r.recorded_on.isoformat(),
                "mm": float(r.mm),
                "source": r.source,
                "note": r.note,
            }
            for r in rows
        ]
    )


@bp.post("/<farm_id>/rainfall")
def create_rainfall(farm_id):
    Farm.query.get_or_404(farm_id)
    payload = request.get_json() or {}
    if "recorded_on" not in payload or "mm" not in payload:
        return jsonify({"error": "recorded_on and mm are required"}), 400

    rainfall = RainfallRecord(
        farm_id=farm_id,
        recorded_on=payload["recorded_on"],
        mm=payload["mm"],
        source=payload.get("source", "manual"),
        note=payload.get("note"),
    )
    db.session.add(rainfall)
    db.session.commit()
    return jsonify({"id": str(rainfall.id)}), 201
