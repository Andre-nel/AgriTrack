from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import AnimalGroupBalance, Mob
from app.models.stock_ledger import StockEventType
from app.services.movement_service import MovementService
from app.services.stock_service import StockService

bp = Blueprint("mobs", __name__)


@bp.get("")
def list_mobs():
    mobs = Mob.query.order_by(Mob.name).all()
    return jsonify(
        [{"id": str(m.id), "farm_id": m.farm_id, "name": m.name, "status": m.status} for m in mobs]
    )


@bp.post("")
def create_mob():
    payload = request.get_json() or {}
    required = {"farm_id", "name"}
    if not required.issubset(payload):
        return jsonify({"error": "farm_id and name are required"}), 400

    mob = Mob(farm_id=payload["farm_id"], name=payload["name"], status=payload.get("status", "active"))
    db.session.add(mob)
    db.session.commit()
    return jsonify({"id": str(mob.id), "name": mob.name}), 201


@bp.get("/<mob_id>")
def get_mob(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    return jsonify({"id": str(mob.id), "farm_id": mob.farm_id, "name": mob.name, "status": mob.status})


@bp.patch("/<mob_id>")
def update_mob(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    payload = request.get_json() or {}

    mob.name = payload.get("name", mob.name)
    requested_status = payload.get("status", mob.status)
    if requested_status == "archived":
        total_head_count = sum(int(balance.head_count) for balance in mob.balances)
        if total_head_count > 0:
            return jsonify({"error": "Mob cannot be deactivated while it still contains stock"}), 400
    mob.status = requested_status
    mob.origin_note = payload.get("origin_note", mob.origin_note)

    db.session.commit()
    return jsonify({"id": str(mob.id), "name": mob.name})


@bp.post("/<mob_id>/animal-groups/adjust")
def adjust_mob_stock(mob_id):
    payload = request.get_json() or {}
    try:
        selected_event_type = StockEventType(payload["event_type"])
        quantity = int(payload["quantity"])
        event_type = selected_event_type
        event_quantity = quantity
        if selected_event_type == StockEventType.count:
            current_balance = AnimalGroupBalance.query.filter_by(
                mob_id=mob_id,
                animal_group_type_id=payload["animal_group_type_id"],
            ).first()
            current_head_count = current_balance.head_count if current_balance else 0
            delta = quantity - current_head_count
            if delta == 0:
                return jsonify({"message": "Count matches current balance. No stock adjustment posted."}), 200
            if delta > 0:
                event_type = StockEventType.adjustment_in
                event_quantity = delta
            else:
                event_type = StockEventType.missing
                event_quantity = -delta

        ledger = StockService.adjust_stock(
            mob_id=mob_id,
            farm_id=payload["farm_id"],
            animal_group_type_id=payload["animal_group_type_id"],
            event_type=event_type,
            quantity=event_quantity,
            note=payload.get("note"),
        )
        db.session.commit()
    except (KeyError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ledger_id": str(ledger.id)}), 201


@bp.get("/<mob_id>/balances")
def mob_balances(mob_id):
    balances = AnimalGroupBalance.query.filter_by(mob_id=mob_id).all()
    return jsonify(
        [
            {
                "animal_group_type_id": b.animal_group_type_id,
                "head_count": b.head_count,
            }
            for b in balances
        ]
    )


@bp.post("/<mob_id>/move")
def move_mob(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    payload = request.get_json() or {}
    allocations = payload.get("allocations", [])

    try:
        session = MovementService.move_mob(mob=mob, allocations=allocations)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"grazing_session_id": str(session.id)}), 201


@bp.post("/<mob_id>/deactivate")
def deactivate_mob(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    total_head_count = sum(int(balance.head_count) for balance in mob.balances)
    if total_head_count > 0:
        return jsonify({"error": "Mob cannot be deactivated while it still contains stock"}), 400

    mob.status = "archived"
    db.session.commit()
    return jsonify({"id": str(mob.id), "status": mob.status}), 200


@bp.post("/<mob_id>/split")
def split_mob(mob_id):
    source = Mob.query.get_or_404(mob_id)
    payload = request.get_json() or {}
    splits = payload.get("splits", [])

    try:
        created = MovementService.split_mob(source_mob=source, splits=splits)
        db.session.commit()
    except (KeyError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"created_mob_ids": [str(m.id) for m in created]}), 201


@bp.post("/merge")
def merge_mobs():
    payload = request.get_json() or {}
    source_ids = payload.get("source_mob_ids", [])
    result_name = payload.get("result_name")

    if not source_ids or not result_name:
        return jsonify({"error": "source_mob_ids and result_name are required"}), 400

    sources = Mob.query.filter(Mob.id.in_(source_ids)).all()
    if len(sources) != len(source_ids):
        return jsonify({"error": "One or more source mobs were not found"}), 404

    try:
        result_mob = MovementService.merge_mobs(source_mobs=sources, result_name=result_name)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"result_mob_id": str(result_mob.id)}), 201
