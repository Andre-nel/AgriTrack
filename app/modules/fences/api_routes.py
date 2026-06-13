from flask import Blueprint, jsonify, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, FenceSection
from app.services.fence_service import FenceService

bp = Blueprint("fences", __name__)


def _section_feature(section: FenceSection) -> dict | None:
    feature = FenceService.map_feature(section)
    if feature:
        feature.setdefault("properties", {})["fence_detail_url"] = url_for(
            "web.farm_fence_detail",
            farm_id=section.farm_id,
            fence_section_id=section.id,
        )
    return feature


def _section_response(section: FenceSection) -> dict:
    return {
        "fence": FenceService.serialize_section(section),
        "feature": _section_feature(section),
    }


@bp.get("")
def list_fences():
    farm_id = (request.args.get("farm_id") or "").strip()
    if farm_id:
        Farm.query.get_or_404(farm_id)
    query = FenceSection.query
    if farm_id:
        query = query.filter_by(farm_id=farm_id)
    sections = query.order_by(FenceSection.section_type.asc(), FenceSection.name.asc()).all()
    return jsonify([FenceService.serialize_section(section) for section in sections])


@bp.post("")
def create_fence():
    payload = request.get_json() or {}
    farm_id = str(payload.get("farm_id") or request.args.get("farm_id") or "").strip()
    if not farm_id:
        return jsonify({"error": "farm_id is required"}), 400
    Farm.query.get_or_404(farm_id)
    try:
        section = FenceService.create_manual_section(farm_id, payload)
        db.session.commit()
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify(_section_response(section)), 201


@bp.get("/<fence_section_id>")
def get_fence(fence_section_id):
    section = FenceSection.query.get_or_404(fence_section_id)
    return jsonify(FenceService.serialize_section(section, include_events=True))


@bp.patch("/<fence_section_id>")
def update_fence(fence_section_id):
    section = FenceSection.query.get_or_404(fence_section_id)
    try:
        FenceService.update_section_from_map(section, request.get_json() or {})
        db.session.commit()
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify(_section_response(section))


@bp.delete("/<fence_section_id>")
def delete_fence(fence_section_id):
    section = FenceSection.query.get_or_404(fence_section_id)
    try:
        FenceService.archive_section(section)
        db.session.commit()
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify(_section_response(section))


@bp.post("/<fence_section_id>/events")
def create_fence_event(fence_section_id):
    section = FenceSection.query.get_or_404(fence_section_id)
    payload = request.get_json() or {}
    try:
        event = FenceService.create_event(
            section=section,
            event_type=payload.get("event_type"),
            description=payload.get("description"),
            raw_tags=payload.get("tags"),
            condition_after=payload.get("condition_after"),
            material_rows=payload.get("materials") or [],
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    return jsonify(FenceService.serialize_event(event)), 201
