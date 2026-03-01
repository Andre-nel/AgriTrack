from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Farm, RainfallRecord

bp = Blueprint("farms", __name__)


@bp.get("")
def list_farms():
    farms = Farm.query.order_by(Farm.name).all()
    return jsonify(
        [
            {
                "id": str(f.id),
                "name": f.name,
                "timezone": f.timezone,
                "active": f.active,
                "default_stocking_rate_ha_per_lsu": float(f.default_stocking_rate_ha_per_lsu),
            }
            for f in farms
        ]
    )


@bp.post("")
def create_farm():
    payload = request.get_json() or {}
    if "name" not in payload:
        return jsonify({"error": "name is required"}), 400

    default_stocking_rate = payload.get("default_stocking_rate_ha_per_lsu", 6)
    try:
        default_stocking_rate = Decimal(str(default_stocking_rate))
        if default_stocking_rate <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        return jsonify({"error": "default_stocking_rate_ha_per_lsu must be greater than 0"}), 400

    farm = Farm(
        name=payload["name"],
        timezone=payload.get("timezone", "UTC"),
        active=True,
        default_stocking_rate_ha_per_lsu=default_stocking_rate,
    )
    db.session.add(farm)
    db.session.commit()
    return jsonify({"id": str(farm.id), "name": farm.name}), 201


@bp.get("/<farm_id>")
def get_farm(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    return jsonify(
        {
            "id": str(farm.id),
            "name": farm.name,
            "timezone": farm.timezone,
            "active": farm.active,
            "default_stocking_rate_ha_per_lsu": float(farm.default_stocking_rate_ha_per_lsu),
        }
    )


@bp.patch("/<farm_id>")
def update_farm(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    payload = request.get_json() or {}

    farm.name = payload.get("name", farm.name)
    farm.timezone = payload.get("timezone", farm.timezone)
    if "active" in payload:
        farm.active = bool(payload["active"])
    if "default_stocking_rate_ha_per_lsu" in payload:
        try:
            value = Decimal(str(payload["default_stocking_rate_ha_per_lsu"]))
            if value <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            return jsonify({"error": "default_stocking_rate_ha_per_lsu must be greater than 0"}), 400
        farm.default_stocking_rate_ha_per_lsu = value

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
    try:
        recorded_on = date.fromisoformat(str(payload["recorded_on"]))
    except ValueError:
        return jsonify({"error": "recorded_on must be a valid ISO date"}), 400

    rainfall = RainfallRecord(
        farm_id=farm_id,
        recorded_on=recorded_on,
        mm=payload["mm"],
        source=payload.get("source", "manual"),
        note=payload.get("note"),
    )
    db.session.add(rainfall)
    db.session.commit()
    return jsonify({"id": str(rainfall.id)}), 201
