from flask import Blueprint, jsonify

from app.models import Farm
from app.services.reporting_service import ReportingService

bp = Blueprint("api_v1", __name__)


@bp.get("/health")
def health():
    return jsonify({"status": "ok", "api": "v1"})


@bp.get("/dashboard")
def dashboard():
    return jsonify(ReportingService.dashboard_summary())


@bp.get("/farms")
def farms():
    rows = Farm.query.order_by(Farm.name).all()
    return jsonify([{"id": str(f.id), "name": f.name, "timezone": f.timezone} for f in rows])
