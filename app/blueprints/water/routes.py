from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import WaterAsset, WaterConnection
from app.services.water_network_service import WaterNetworkService


bp = Blueprint("water_api", __name__)


@bp.get("/water-assets")
def list_water_assets():
    query = WaterAsset.query.order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
    farm_id = (request.args.get("farm_id") or "").strip()
    if farm_id:
        query = query.filter(WaterAsset.farm_id == farm_id)
    return jsonify([WaterNetworkService.serialize_asset(asset) for asset in query.all()])


@bp.post("/water-assets")
def create_water_asset():
    payload = request.get_json() or {}
    try:
        asset = WaterNetworkService.create_asset(payload)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to save water asset"}), 400
    return jsonify(WaterNetworkService.serialize_asset(asset)), 201


@bp.get("/water-assets/<asset_id>")
def get_water_asset(asset_id):
    asset = WaterAsset.query.get_or_404(asset_id)
    return jsonify(WaterNetworkService.serialize_asset(asset))


@bp.patch("/water-assets/<asset_id>")
def update_water_asset(asset_id):
    asset = WaterAsset.query.get_or_404(asset_id)
    payload = request.get_json() or {}
    try:
        WaterNetworkService.update_asset(asset, payload)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to update water asset"}), 400
    return jsonify(WaterNetworkService.serialize_asset(asset))


@bp.get("/water-connections")
def list_water_connections():
    query = WaterConnection.query.order_by(WaterConnection.flow_type.asc(), WaterConnection.created_at.asc())
    farm_id = (request.args.get("farm_id") or "").strip()
    if farm_id:
        query = query.filter(WaterConnection.farm_id == farm_id)
    return jsonify([WaterNetworkService.serialize_connection(connection) for connection in query.all()])


@bp.post("/water-connections")
def create_water_connection():
    payload = request.get_json() or {}
    try:
        connection = WaterNetworkService.create_connection(payload)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to save water connection"}), 400
    return jsonify(WaterNetworkService.serialize_connection(connection)), 201


@bp.get("/water-connections/<connection_id>")
def get_water_connection(connection_id):
    connection = WaterConnection.query.get_or_404(connection_id)
    return jsonify(WaterNetworkService.serialize_connection(connection))


@bp.patch("/water-connections/<connection_id>")
def update_water_connection(connection_id):
    connection = WaterConnection.query.get_or_404(connection_id)
    payload = request.get_json() or {}
    try:
        WaterNetworkService.update_connection(connection, payload)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Unable to update water connection"}), 400
    return jsonify(WaterNetworkService.serialize_connection(connection))


@bp.delete("/water-connections/<connection_id>")
def delete_water_connection(connection_id):
    connection = WaterConnection.query.get_or_404(connection_id)
    db.session.delete(connection)
    db.session.commit()
    return ("", 204)
