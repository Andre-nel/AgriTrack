from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import Farm
from app.models.orchard import Orchard, OrchardEmitter, OrchardLine, OrchardPlant, OrchardRow
from app.modules.orchards import services as service
from app.modules.orchards import validation as v
from app.modules.orchards.schemas import snapshot

bp = Blueprint("orchards", __name__)
API = "/api/farms/<farm_id>/orchards/<orchard_id>"


def _orchard(farm_id, orchard_id):
    return Orchard.query.filter_by(farm_id=farm_id, id=orchard_id).first_or_404()


def mutation(fn):
    """Claim the orchard revision before reading children; commit all or nothing."""
    @wraps(fn)
    def wrapped(farm_id, orchard_id, **kwargs):
        orchard = _orchard(farm_id, orchard_id)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="A JSON object is required."), 400
        try:
            service.claim_revision(orchard, payload.get("revision"))
            fn(orchard, payload, **kwargs)
            db.session.flush()
            result = snapshot(orchard)
            db.session.commit()
            return jsonify(result)
        except service.RevisionConflict as exc:
            db.session.rollback()
            return jsonify(error=str(exc)), 409
        except ValueError as exc:
            db.session.rollback()
            return jsonify(error=str(exc)), 400
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="The change conflicts with an occupied space or related record. Refresh and try again."), 409
        except OperationalError as exc:
            db.session.rollback()
            if "locked" not in str(exc).lower():
                raise
            return jsonify(error="The database is busy. Your edits have not been saved; retry shortly."), 503
    return wrapped


@bp.get("/orchards")
def index():
    farm_id = request.args.get("farm_id")
    farm = Farm.query.get_or_404(farm_id) if farm_id else None
    query = Orchard.query
    if farm:
        query = query.filter_by(farm_id=farm.id)
    return render_template("orchards/index.html", orchards=query.order_by(Orchard.name).all(), farm=farm, farms=Farm.query.order_by(Farm.name).all())


@bp.get("/farms/<farm_id>/orchards/<orchard_id>")
def workspace(farm_id, orchard_id):
    orchard = _orchard(farm_id, orchard_id)
    return render_template("orchards/workspace.html", orchard=orchard, today=datetime.now(timezone(timedelta(hours=2))).date().isoformat())


@bp.post("/api/farms/<farm_id>/orchards")
def create(farm_id):
    Farm.query.get_or_404(farm_id)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="A JSON object is required."), 400
    try:
        orchard = Orchard(farm_id=farm_id, name=v.label(payload.get("name")))
        db.session.add(orchard)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    return jsonify(snapshot(orchard)), 201


@bp.get(API)
def data(farm_id, orchard_id):
    response = jsonify(snapshot(_orchard(farm_id, orchard_id)))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post(API + "/rows")
@mutation
def add_row(orchard, payload):
    service.create_row(orchard, payload)


@bp.patch(API + "/rows/<row_id>")
@mutation
def edit_row(orchard, payload, row_id):
    row = service.owned(OrchardRow, orchard.id, row_id)
    service.edit_row(row, payload)
    if "resize" in payload:
        if not isinstance(payload["resize"], dict):
            raise ValueError("Resize must be an object.")
        service.resize_row(row, payload["resize"])


@bp.post(API + "/rows/<row_id>/resize")
@mutation
def resize_row(orchard, payload, row_id):
    service.resize_row(service.owned(OrchardRow, orchard.id, row_id), payload)


@bp.delete(API + "/rows/<row_id>")
@mutation
def delete_row(orchard, payload, row_id):
    service.delete_row(service.owned(OrchardRow, orchard.id, row_id))


@bp.post(API + "/plants")
@mutation
def add_stock(orchard, payload):
    service.add_stock(orchard, payload)


@bp.patch(API + "/plants/<plant_id>")
@mutation
def edit_plant(orchard, payload, plant_id):
    plant = service.owned(OrchardPlant, orchard.id, plant_id)
    plant.name = v.label(payload.get("name"), limit=160)


@bp.delete(API + "/plants/<plant_id>")
@mutation
def remove_stock(orchard, payload, plant_id):
    service.remove_stock(service.owned(OrchardPlant, orchard.id, plant_id))


@bp.post(API + "/plants/<plant_id>/placement")
@mutation
def placement(orchard, payload, plant_id):
    plant = service.owned(OrchardPlant, orchard.id, plant_id)
    if "name" in payload:
        plant.name = v.label(payload["name"], limit=160)
    service.place_plant(plant, payload)


@bp.post(API + "/lines")
@mutation
def add_line(orchard, payload):
    service.save_line(orchard, payload)


@bp.patch(API + "/lines/<line_id>")
@mutation
def edit_line(orchard, payload, line_id):
    service.save_line(orchard, payload, service.owned(OrchardLine, orchard.id, line_id))


@bp.delete(API + "/lines/<line_id>")
@mutation
def delete_line(orchard, payload, line_id):
    db.session.delete(service.owned(OrchardLine, orchard.id, line_id))


@bp.post(API + "/lines/<line_id>/emitters")
@mutation
def add_emitter(orchard, payload, line_id):
    service.save_emitter(service.owned(OrchardLine, orchard.id, line_id), payload)


@bp.patch(API + "/emitters/<emitter_id>")
@mutation
def edit_emitter(orchard, payload, emitter_id):
    emitter = service.owned(OrchardEmitter, orchard.id, emitter_id)
    service.save_emitter(service.owned(OrchardLine, orchard.id, emitter.line_id), payload, emitter)


@bp.delete(API + "/emitters/<emitter_id>")
@mutation
def delete_emitter(orchard, payload, emitter_id):
    db.session.delete(service.owned(OrchardEmitter, orchard.id, emitter_id))
