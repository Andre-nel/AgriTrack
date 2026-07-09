from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from app.core.auth import safe_next_url
from app.extensions import db
from app.models import Farm, Incident
from app.services.incident_service import IncidentService

bp = Blueprint("incidents", __name__)


def _web_actor_name() -> str:
    user = getattr(g, "web_user", None)
    if user is not None and user.name:
        return user.name
    return "Web User"


def _load_incident(incident_id: str) -> Incident:
    return (
        Incident.query.options(selectinload(Incident.farm))
        .filter_by(id=incident_id)
        .first_or_404()
    )


def _incident_form_values(source) -> dict:
    return {
        "farm_id": (source.get("farm_id") or "").strip(),
        "occurred_on": (source.get("occurred_on") or date.today().isoformat()).strip(),
        "category": (source.get("category") or "").strip(),
        "note": (source.get("note") or "").strip(),
        "tags": (source.get("tags") or "").strip(),
        "reported_by": (source.get("reported_by") or _web_actor_name()).strip(),
    }


def _filter_values() -> dict:
    return {
        "farm_id": (request.args.get("farm_id") or "").strip(),
        "q": (request.args.get("q") or "").strip(),
        "tag": (request.args.get("tag") or "").strip(),
        "category": (request.args.get("category") or "").strip(),
        "start_date": (request.args.get("start_date") or "").strip(),
        "end_date": (request.args.get("end_date") or "").strip(),
    }


def _available_tags(incidents: list[Incident]) -> list[str]:
    tags = set()
    for incident in incidents:
        tags.update(IncidentService.tags_from_csv(incident.tags_csv))
    return sorted(tags)


@bp.get("/incidents")
def index():
    farms = Farm.query.order_by(Farm.name).all()
    filters = _filter_values()
    incidents = IncidentService.search_incidents(
        farm_id=filters["farm_id"],
        query=filters["q"],
        tag=filters["tag"],
        category=filters["category"],
        start_date=filters["start_date"],
        end_date=filters["end_date"],
    )
    all_incidents_for_tags_query = Incident.query
    if filters["farm_id"]:
        all_incidents_for_tags_query = all_incidents_for_tags_query.filter(Incident.farm_id == filters["farm_id"])
    all_incidents_for_tags = all_incidents_for_tags_query.all()
    category_counts = {}
    for incident in incidents:
        category_counts[incident.category] = category_counts.get(incident.category, 0) + 1
    category_rows = sorted(category_counts.items(), key=lambda item: item[0].lower())
    return render_template(
        "incidents/index.html",
        farms=farms,
        filters=filters,
        form_values=_incident_form_values(request.args),
        incidents=incidents,
        incident_rows=[
            {
                "incident": incident,
                "tags": IncidentService.tags_from_csv(incident.tags_csv),
                "detail_url": url_for("incidents.detail", incident_id=incident.id),
            }
            for incident in incidents
        ],
        available_tags=_available_tags(all_incidents_for_tags),
        category_rows=category_rows,
        summary={
            "total_count": len(incidents),
            "category_count": len(category_rows),
        },
    )


@bp.post("/incidents")
def create_incident():
    next_url = (request.form.get("next") or "").strip()
    try:
        farm_id = (request.form.get("farm_id") or "").strip()
        if not Farm.query.filter_by(id=farm_id).first():
            raise ValueError("Incident farm is required")
        incident = IncidentService.create_incident(
            farm_id=farm_id,
            occurred_on=request.form.get("occurred_on"),
            category=request.form.get("category"),
            note=request.form.get("note"),
            raw_tags=request.form.get("tags"),
            reported_by=request.form.get("reported_by") or _web_actor_name(),
        )
        db.session.commit()
        flash(f"Incident '{incident.category}' recorded", "success")
        if next_url:
            return redirect(safe_next_url(next_url))
        return redirect(url_for("incidents.detail", incident_id=incident.id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        if next_url:
            return redirect(safe_next_url(next_url))
        return redirect(url_for("incidents.index", **_filter_values()))


@bp.get("/incidents/<incident_id>")
def detail(incident_id: str):
    incident = _load_incident(incident_id)
    return render_template(
        "incidents/detail.html",
        incident=incident,
        tags=IncidentService.tags_from_csv(incident.tags_csv),
        farms=Farm.query.order_by(Farm.name).all(),
        back_url=url_for(
            "incidents.index",
            farm_id=incident.farm_id,
            category=incident.category,
        ),
    )


@bp.post("/incidents/<incident_id>/edit")
def edit_incident(incident_id: str):
    incident = _load_incident(incident_id)
    try:
        farm_id = (request.form.get("farm_id") or "").strip()
        if not Farm.query.filter_by(id=farm_id).first():
            raise ValueError("Incident farm is required")
        IncidentService.update_incident(
            incident=incident,
            farm_id=farm_id,
            occurred_on=request.form.get("occurred_on"),
            category=request.form.get("category"),
            note=request.form.get("note"),
            raw_tags=request.form.get("tags"),
            reported_by=request.form.get("reported_by") or _web_actor_name(),
        )
        db.session.commit()
        flash("Incident updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("incidents.detail", incident_id=incident.id))


@bp.post("/incidents/<incident_id>/delete")
def delete_incident(incident_id: str):
    incident = _load_incident(incident_id)
    redirect_url = url_for(
        "incidents.index",
        farm_id=incident.farm_id,
        category=incident.category,
    )
    db.session.delete(incident)
    db.session.commit()
    flash("Incident deleted", "success")
    return redirect(redirect_url)
