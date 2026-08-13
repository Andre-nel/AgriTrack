from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AnimalGroupBalance, Mob, MobEvent
from app.models.stock_ledger import StockEventType
from app.services.mob_event_service import MobEventService
from app.services.mob_service import MobService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService

bp = Blueprint("mobs", __name__)


def _get_active_mob_or_404(mob_id: str) -> Mob:
    return Mob.query.filter_by(id=mob_id, status="active").first_or_404()


@bp.get("")
def list_mobs():
    mobs = Mob.query.filter_by(status="active").order_by(Mob.name).all()
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
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Active mob name already exists on this farm"}), 400
    return jsonify({"id": str(mob.id), "name": mob.name}), 201


@bp.get("/<mob_id>")
def get_mob(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    return jsonify({"id": str(mob.id), "farm_id": mob.farm_id, "name": mob.name, "status": mob.status})


@bp.patch("/<mob_id>")
def update_mob(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    payload = request.get_json() or {}

    mob.name = payload.get("name", mob.name)
    requested_status = payload.get("status", mob.status)
    if requested_status == "archived":
        total_head_count = sum(int(balance.head_count) for balance in mob.balances)
        if total_head_count > 0:
            return jsonify({"error": "Mob cannot be deactivated while it still contains stock"}), 400
        MobService.archive_mob(mob)
    else:
        mob.status = requested_status
    mob.origin_note = payload.get("origin_note", mob.origin_note)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Active mob name already exists on this farm"}), 400
    return jsonify({"id": str(mob.id), "name": mob.name})


@bp.post("/<mob_id>/animal-groups/adjust")
def adjust_mob_stock(mob_id):
    mob = _get_active_mob_or_404(mob_id)
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
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=payload["animal_group_type_id"],
            event_type=event_type,
            quantity=event_quantity,
            note=payload.get("note"),
            allocation_paddock_id=payload.get("allocation_paddock_id") or payload.get("paddock_id"),
        )
        db.session.commit()
    except (KeyError, ValueError) as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ledger_id": str(ledger.id)}), 201


@bp.get("/<mob_id>/balances")
def mob_balances(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    balances = (
        AnimalGroupBalance.query.filter(
            AnimalGroupBalance.mob_id == mob.id,
            AnimalGroupBalance.head_count > 0,
        )
        .all()
    )
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
    mob = _get_active_mob_or_404(mob_id)
    payload = request.get_json() or {}
    allocations = payload.get("allocations", [])
    destination_farm_id = payload.get("destination_farm_id")

    try:
        session = MovementService.move_mob(
            mob=mob,
            allocations=allocations,
            destination_farm_id=destination_farm_id,
            allocation_mode=payload.get("allocation_mode"),
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Move failed: active mob name already exists on the destination farm"}), 400
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"grazing_session_id": str(session.id)}), 201


@bp.post("/map-species-move")
def move_map_species():
    payload = request.get_json() or {}
    try:
        result = MovementService.move_species_between_paddocks(
            source_paddock_id=payload.get("source_paddock_id"),
            target_paddock_id=payload.get("target_paddock_id"),
            species=payload.get("species"),
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Move failed: active mob name already exists on the destination farm"}), 400
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify(result), 200


@bp.post("/<mob_id>/events")
def create_mob_event(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    payload = request.get_json() or {}
    raw_tags = payload.get("tags")
    if isinstance(raw_tags, list):
        raw_tags = ",".join(str(value) for value in raw_tags)
    description = payload.get("description")

    try:
        event = MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=description,
            raw_tags=raw_tags,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"event_id": str(event.id)}), 201


@bp.get("/<mob_id>/events")
def list_mob_events(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    tag_filter = " ".join((request.args.get("tag") or "").strip().lower().split())
    rows = (
        MobEvent.query.filter_by(mob_id=mob.id)
        .order_by(MobEvent.event_at.desc(), MobEvent.created_at.desc())
        .all()
    )
    events = []
    for row in rows:
        tags = MobEventService.tags_from_csv(row.tags_csv)
        if tag_filter and tag_filter not in tags:
            continue
        events.append(
            {
                "id": str(row.id),
                "event_at": row.event_at.isoformat() if row.event_at else None,
                "tags": tags,
                "description": row.description,
            }
        )

    return jsonify({"mob_id": str(mob.id), "tag_filter": tag_filter, "events": events})


@bp.post("/<mob_id>/transfer")
def transfer_mob_stock(mob_id):
    source = _get_active_mob_or_404(mob_id)
    payload = request.get_json() or {}
    destination_mob_id = str(payload.get("destination_mob_id") or "").strip()
    destination_farm_id = payload.get("destination_farm_id")
    transfers = payload.get("transfers", [])
    note = payload.get("note")

    if not destination_mob_id:
        return jsonify({"error": "destination_mob_id is required"}), 400

    destination = Mob.query.filter_by(id=destination_mob_id, status="active").first()
    if not destination:
        return jsonify({"error": "Destination mob is invalid"}), 400

    try:
        MovementService.transfer_stock_between_mobs(
            source_mob=source,
            destination_mob=destination,
            transfers=transfers,
            destination_farm_id=destination_farm_id,
            note=note,
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Transfer failed due to a concurrent stock update. Please retry."}), 400
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"status": "ok"}), 200


@bp.post("/<mob_id>/deactivate")
def deactivate_mob(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    total_head_count = sum(int(balance.head_count) for balance in mob.balances)
    if total_head_count > 0:
        return jsonify({"error": "Mob cannot be deactivated while it still contains stock"}), 400

    MobService.archive_mob(mob)
    db.session.commit()
    return jsonify({"id": str(mob.id), "status": mob.status}), 200


@bp.post("/<mob_id>/split")
def split_mob(mob_id):
    source = _get_active_mob_or_404(mob_id)
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

    sources = Mob.query.filter(Mob.id.in_(source_ids), Mob.status == "active").all()
    if len(sources) != len(source_ids):
        return jsonify({"error": "One or more source mobs were not found"}), 404

    try:
        result_mob = MovementService.merge_mobs(source_mobs=sources, result_name=result_name)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"result_mob_id": str(result_mob.id)}), 201
