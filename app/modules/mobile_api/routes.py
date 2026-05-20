import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingSession,
    MobileAuthToken,
    MobileSyncCommand,
    Mob,
    MobEvent,
    Paddock,
    RainfallRecord,
    User,
    WaterAsset,
    WaterConnection,
)
from app.models.stock_ledger import StockEventType
from app.services.mob_event_service import MobEventService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService
from app.services.water_network_service import WaterNetworkService

bp = Blueprint("mobile_api", __name__)

TOKEN_TTL_DAYS = 90
MAX_COMMANDS_PER_REQUEST = 100


class MobileApiError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_aware(value).isoformat()


def _api_error(code: str, message: str, status_code: int):
    return jsonify({"error": {"code": code, "message": message}}), status_code


@bp.errorhandler(MobileApiError)
def _handle_mobile_error(exc: MobileApiError):
    return _api_error(exc.code, exc.message, exc.status_code)


def _json_payload() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise MobileApiError("invalid_json", "Request body must be a JSON object")
    return payload


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_device_name(value: str | None) -> str:
    text = " ".join((value or "").strip().split())
    return text[:120] or "Android device"


def _farm_roles(user: User) -> list[dict]:
    roles = []
    user_roles = (role for role in user.farm_roles if role.farm is not None)
    for role in sorted(user_roles, key=lambda item: item.farm.name.lower()):
        roles.append(
            {
                "farm_id": str(role.farm_id),
                "farm_name": role.farm.name,
                "role": role.role,
                "active": role.farm.active,
            }
        )
    return roles


def _serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "active": user.active,
        "farm_roles": _farm_roles(user),
    }


def _has_farm_access(user: User, farm_id: str) -> bool:
    return any(
        str(role.farm_id) == str(farm_id) and role.farm is not None and role.farm.active
        for role in user.farm_roles
    )


def _get_accessible_farm(farm_id: str) -> Farm:
    farm = db.session.get(Farm, farm_id)
    if farm is None:
        raise MobileApiError("not_found", "Farm not found", 404)
    if not _has_farm_access(g.mobile_user, farm_id):
        raise MobileApiError("forbidden", "You do not have access to this farm", 403)
    return farm


def _parse_iso_date(value, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc


def _parse_iso_datetime(value, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coerce_non_negative_decimal(value, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid number") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return parsed


def _serialize_farm(farm: Farm) -> dict:
    return {
        "id": str(farm.id),
        "name": farm.name,
        "timezone": farm.timezone,
        "active": farm.active,
        "default_stocking_rate_ha_per_lsu": float(farm.default_stocking_rate_ha_per_lsu),
        "updated_at": _iso_datetime(farm.updated_at),
    }


def _serialize_paddock(paddock: Paddock) -> dict:
    return {
        "id": str(paddock.id),
        "farm_id": str(paddock.farm_id),
        "name": paddock.name,
        "area_ha": float(paddock.area_ha or 0),
        "grazeable_area_ha": float(paddock.grazeable_area_ha or 0),
        "status": paddock.status,
        "stocking_rate_ha_per_lsu_override": (
            float(paddock.stocking_rate_ha_per_lsu_override)
            if paddock.stocking_rate_ha_per_lsu_override is not None
            else None
        ),
        "updated_at": _iso_datetime(paddock.updated_at),
    }


def _serialize_animal_group_type(group_type: AnimalGroupType) -> dict:
    return {
        "id": str(group_type.id),
        "species": group_type.species,
        "breed": group_type.breed,
        "sex": group_type.sex,
        "age_class": group_type.age_class,
    }


def _serialize_balance(balance: AnimalGroupBalance) -> dict:
    return {
        "id": str(balance.id),
        "mob_id": str(balance.mob_id),
        "animal_group_type_id": str(balance.animal_group_type_id),
        "animal_group_type": _serialize_animal_group_type(balance.animal_group_type),
        "head_count": int(balance.head_count),
        "updated_at": _iso_datetime(balance.updated_at),
    }


def _serialize_mob(mob: Mob) -> dict:
    balances = sorted(
        (balance for balance in mob.balances if int(balance.head_count) > 0),
        key=lambda balance: (
            balance.animal_group_type.species,
            balance.animal_group_type.breed,
            balance.animal_group_type.sex,
            balance.animal_group_type.age_class,
        ),
    )
    return {
        "id": str(mob.id),
        "farm_id": str(mob.farm_id),
        "name": mob.name,
        "status": mob.status,
        "origin_note": mob.origin_note,
        "balances": [_serialize_balance(balance) for balance in balances],
        "updated_at": _iso_datetime(mob.updated_at),
    }


def _serialize_grazing_session(session: GrazingSession) -> dict:
    return {
        "id": str(session.id),
        "farm_id": str(session.farm_id),
        "mob_id": str(session.mob_id),
        "start_at": _iso_datetime(session.start_at),
        "end_at": _iso_datetime(session.end_at),
        "allocations": [
            {
                "paddock_id": str(allocation.paddock_id),
                "allocation_fraction": float(allocation.allocation_fraction),
            }
            for allocation in sorted(session.allocations, key=lambda item: item.paddock.name.lower())
        ],
    }


def _serialize_rainfall(record: RainfallRecord) -> dict:
    return {
        "id": str(record.id),
        "farm_id": str(record.farm_id),
        "recorded_on": record.recorded_on.isoformat(),
        "mm": float(record.mm),
        "source": record.source,
        "note": record.note,
        "updated_at": _iso_datetime(record.updated_at),
    }


def _serialize_mob_event(event: MobEvent) -> dict:
    return {
        "id": str(event.id),
        "farm_id": str(event.farm_id),
        "mob_id": str(event.mob_id),
        "event_at": _iso_datetime(event.event_at),
        "tags": MobEventService.tags_from_csv(event.tags_csv),
        "description": event.description,
        "updated_at": _iso_datetime(event.updated_at),
    }


@bp.before_request
def _authenticate_mobile_request():
    if request.endpoint == "mobile_api.login":
        return None

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return _api_error("missing_token", "Bearer token is required", 401)

    token_record = MobileAuthToken.query.filter_by(token_hash=_hash_token(token)).first()
    if token_record is None or token_record.user is None or not token_record.user.active:
        return _api_error("invalid_token", "Bearer token is invalid", 401)
    if token_record.revoked_at is not None:
        return _api_error("revoked_token", "Bearer token has been revoked", 401)
    if _as_aware(token_record.expires_at) <= _utcnow():
        return _api_error("expired_token", "Bearer token has expired", 401)

    token_record.last_used_at = _utcnow()
    g.mobile_token = token_record
    g.mobile_user = token_record.user
    return None


@bp.post("/auth/login")
def login():
    payload = _json_payload()
    email = " ".join(str(payload.get("email") or "").strip().lower().split())
    password = str(payload.get("password") or "")
    if not email or not password:
        return _api_error("invalid_credentials", "Email and password are required", 401)

    user = User.query.filter(func.lower(User.email) == email).first()
    if user is None or not user.active or not user.check_password(password):
        return _api_error("invalid_credentials", "Email or password is incorrect", 401)

    raw_token = secrets.token_urlsafe(32)
    now = _utcnow()
    token_record = MobileAuthToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        token_prefix=raw_token[:12],
        device_name=_normalize_device_name(payload.get("device_name")),
        expires_at=now + timedelta(days=TOKEN_TTL_DAYS),
        last_used_at=now,
    )
    db.session.add(token_record)
    db.session.commit()

    return jsonify(
        {
            "token": raw_token,
            "token_type": "Bearer",
            "expires_at": _iso_datetime(token_record.expires_at),
            "user": _serialize_user(user),
            "farms": [
                _serialize_farm(role.farm)
                for role in user.farm_roles
                if role.farm is not None and role.farm.active
            ],
        }
    )


@bp.post("/auth/logout")
def logout():
    g.mobile_token.revoked_at = _utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


@bp.get("/bootstrap")
def bootstrap():
    user = g.mobile_user
    return jsonify(
        {
            "server_time": _iso_datetime(_utcnow()),
            "user": _serialize_user(user),
            "farms": [
                _serialize_farm(role.farm)
                for role in user.farm_roles
                if role.farm is not None and role.farm.active
            ],
            "animal_group_types": [
                _serialize_animal_group_type(group_type)
                for group_type in AnimalGroupType.query.order_by(
                    AnimalGroupType.species,
                    AnimalGroupType.breed,
                    AnimalGroupType.sex,
                    AnimalGroupType.age_class,
                ).all()
            ],
            "sync": {
                "supported_command_types": [
                    "rainfall.create",
                    "mob_event.create",
                    "stock_count.record",
                    "mob.move",
                    "water_asset_status.update",
                ],
                "max_commands_per_request": MAX_COMMANDS_PER_REQUEST,
            },
        }
    )


@bp.get("/farms/<farm_id>/snapshot")
def farm_snapshot(farm_id):
    farm = _get_accessible_farm(farm_id)
    paddocks = Paddock.query.filter_by(farm_id=farm.id).order_by(Paddock.name.asc()).all()
    mobs = Mob.query.filter_by(farm_id=farm.id).order_by(Mob.name.asc()).all()
    active_grazing = (
        GrazingSession.query.filter_by(farm_id=farm.id, end_at=None)
        .order_by(GrazingSession.start_at.desc())
        .all()
    )
    rainfall = (
        RainfallRecord.query.filter_by(farm_id=farm.id)
        .order_by(RainfallRecord.recorded_on.desc())
        .limit(120)
        .all()
    )
    mob_events = (
        MobEvent.query.filter_by(farm_id=farm.id)
        .order_by(MobEvent.event_at.desc(), MobEvent.created_at.desc())
        .limit(200)
        .all()
    )
    water_assets = (
        WaterAsset.query.filter_by(farm_id=farm.id)
        .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
        .all()
    )
    water_connections = (
        WaterConnection.query.filter_by(farm_id=farm.id)
        .order_by(WaterConnection.flow_type.asc(), WaterConnection.created_at.asc())
        .all()
    )
    network_state = WaterNetworkService.network_state_for_farm(str(farm.id))

    return jsonify(
        {
            "server_time": _iso_datetime(_utcnow()),
            "farm": _serialize_farm(farm),
            "paddocks": [_serialize_paddock(paddock) for paddock in paddocks],
            "mobs": [_serialize_mob(mob) for mob in mobs],
            "active_grazing": [
                _serialize_grazing_session(session) for session in active_grazing
            ],
            "rainfall": [_serialize_rainfall(record) for record in rainfall],
            "mob_events": [_serialize_mob_event(event) for event in mob_events],
            "water_assets": [
                WaterNetworkService.serialize_asset(asset, network_state=network_state)
                for asset in water_assets
            ],
            "water_connections": [
                WaterNetworkService.serialize_connection(connection)
                for connection in water_connections
            ],
        }
    )


def _command_failure_result(
    *,
    client_command_id: str | None,
    command_type: str | None,
    code: str,
    message: str,
    duplicate: bool = False,
) -> dict:
    return {
        "client_command_id": client_command_id,
        "type": command_type,
        "status": "failed",
        "duplicate": duplicate,
        "response": None,
        "error": {"code": code, "message": message},
    }


def _serialize_command_record(record: MobileSyncCommand, *, duplicate: bool = False) -> dict:
    return {
        "client_command_id": record.client_command_id,
        "type": record.command_type,
        "status": record.status,
        "duplicate": duplicate,
        "response": record.response_payload,
        "error": (
            {"code": record.error_code, "message": record.error_message}
            if record.status == "failed"
            else None
        ),
        "processed_at": _iso_datetime(record.processed_at),
    }


@bp.post("/sync/commands")
def sync_commands():
    payload = _json_payload()
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return _api_error("invalid_payload", "commands must be a list", 400)
    if len(commands) > MAX_COMMANDS_PER_REQUEST:
        return _api_error(
            "too_many_commands",
            f"No more than {MAX_COMMANDS_PER_REQUEST} commands are allowed per request",
            400,
        )

    return jsonify({"results": [_process_command(command) for command in commands]})


def _process_command(command) -> dict:
    if not isinstance(command, dict):
        return _command_failure_result(
            client_command_id=None,
            command_type=None,
            code="invalid_command",
            message="Each command must be a JSON object",
        )

    client_command_id = str(command.get("client_command_id") or "").strip()
    command_type = str(command.get("type") or "").strip()
    farm_id = str(command.get("farm_id") or "").strip()
    if not client_command_id:
        return _command_failure_result(
            client_command_id=None,
            command_type=command_type or None,
            code="invalid_command",
            message="client_command_id is required",
        )
    if len(client_command_id) > 120:
        return _command_failure_result(
            client_command_id=client_command_id,
            command_type=command_type or None,
            code="invalid_command",
            message="client_command_id must be 120 characters or fewer",
        )

    existing = MobileSyncCommand.query.filter_by(
        user_id=g.mobile_user.id,
        client_command_id=client_command_id,
    ).first()
    if existing is not None:
        return _serialize_command_record(existing, duplicate=True)

    now = _utcnow()
    try:
        if not command_type:
            raise MobileApiError("invalid_command", "type is required")
        if not farm_id:
            raise MobileApiError("invalid_command", "farm_id is required")
        farm = _get_accessible_farm(farm_id)
        response = _dispatch_command(command_type, farm, command.get("payload") or {})
        record = MobileSyncCommand(
            user_id=g.mobile_user.id,
            farm_id=farm.id,
            client_command_id=client_command_id,
            command_type=command_type,
            status="applied",
            request_payload=command,
            response_payload=response,
            processed_at=now,
        )
        db.session.add(record)
        db.session.commit()
        return _serialize_command_record(record)
    except (IntegrityError, MobileApiError, ValueError) as exc:
        db.session.rollback()
        code = exc.code if isinstance(exc, MobileApiError) else "invalid_command"
        message = exc.message if isinstance(exc, MobileApiError) else str(exc)
        return _record_failed_command(
            command=command,
            client_command_id=client_command_id,
            command_type=command_type,
            farm_id=farm_id,
            code=code,
            message=message,
            processed_at=now,
        )


def _record_failed_command(
    *,
    command: dict,
    client_command_id: str,
    command_type: str,
    farm_id: str,
    code: str,
    message: str,
    processed_at: datetime,
) -> dict:
    if not farm_id or db.session.get(Farm, farm_id) is None:
        return _command_failure_result(
            client_command_id=client_command_id,
            command_type=command_type or None,
            code=code,
            message=message,
        )

    record = MobileSyncCommand(
        user_id=g.mobile_user.id,
        farm_id=farm_id,
        client_command_id=client_command_id,
        command_type=command_type or "unknown",
        status="failed",
        request_payload=command,
        error_code=code,
        error_message=message,
        processed_at=processed_at,
    )
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = MobileSyncCommand.query.filter_by(
            user_id=g.mobile_user.id,
            client_command_id=client_command_id,
        ).first()
        if existing is not None:
            return _serialize_command_record(existing, duplicate=True)
        raise
    return _serialize_command_record(record)


def _dispatch_command(command_type: str, farm: Farm, payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    handlers = {
        "rainfall.create": _handle_rainfall_create,
        "mob_event.create": _handle_mob_event_create,
        "stock_count.record": _handle_stock_count_record,
        "mob.move": _handle_mob_move,
        "water_asset_status.update": _handle_water_asset_status_update,
    }
    handler = handlers.get(command_type)
    if handler is None:
        raise MobileApiError("unsupported_command", f"Unsupported command type: {command_type}")
    return handler(farm, payload)


def _handle_rainfall_create(farm: Farm, payload: dict) -> dict:
    recorded_on = _parse_iso_date(payload.get("recorded_on"), "recorded_on")
    mm = _coerce_non_negative_decimal(payload.get("mm"), "mm")
    rainfall = RainfallRecord(
        farm_id=farm.id,
        recorded_on=recorded_on,
        mm=mm,
        source=str(payload.get("source") or "mobile")[:30],
        note=payload.get("note"),
    )
    db.session.add(rainfall)
    db.session.flush()
    return {"rainfall_id": str(rainfall.id)}


def _get_active_mob_for_farm(farm: Farm, mob_id: str) -> Mob:
    mob = Mob.query.filter_by(id=mob_id, farm_id=farm.id, status="active").first()
    if mob is None:
        raise MobileApiError("not_found", "Mob not found for this farm", 404)
    return mob


def _handle_mob_event_create(farm: Farm, payload: dict) -> dict:
    mob = _get_active_mob_for_farm(farm, str(payload.get("mob_id") or "").strip())
    raw_tags = payload.get("tags")
    if isinstance(raw_tags, list):
        raw_tags = ",".join(str(value) for value in raw_tags)
    event = MobEventService.create_event(
        mob_id=mob.id,
        farm_id=farm.id,
        description=payload.get("description"),
        raw_tags=raw_tags,
        event_at=_parse_iso_datetime(payload.get("event_at"), "event_at"),
    )
    db.session.flush()
    return {"event_id": str(event.id)}


def _handle_stock_count_record(farm: Farm, payload: dict) -> dict:
    mob = _get_active_mob_for_farm(farm, str(payload.get("mob_id") or "").strip())
    group_id = str(payload.get("animal_group_type_id") or "").strip()
    if not group_id:
        raise ValueError("animal_group_type_id is required")
    if db.session.get(AnimalGroupType, group_id) is None:
        raise ValueError("animal_group_type_id is invalid")
    try:
        counted_quantity = int(payload.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be a whole number") from exc
    if counted_quantity < 0:
        raise ValueError("quantity must be greater than or equal to 0")

    current_balance = AnimalGroupBalance.query.filter_by(
        mob_id=mob.id,
        animal_group_type_id=group_id,
    ).first()
    current_head_count = int(current_balance.head_count) if current_balance else 0
    delta = counted_quantity - current_head_count
    if delta == 0:
        return {
            "mob_id": str(mob.id),
            "animal_group_type_id": group_id,
            "head_count": counted_quantity,
            "message": "Count matches current balance. No stock adjustment posted.",
        }

    event_type = StockEventType.adjustment_in if delta > 0 else StockEventType.missing
    ledger = StockService.adjust_stock(
        mob_id=mob.id,
        farm_id=farm.id,
        animal_group_type_id=group_id,
        event_type=event_type,
        quantity=abs(delta),
        note=payload.get("note") or "mobile stock count",
        event_time=_parse_iso_datetime(payload.get("event_time"), "event_time"),
    )
    db.session.flush()
    return {
        "ledger_id": str(ledger.id),
        "event_type": event_type.value,
        "quantity": abs(delta),
        "head_count": counted_quantity,
    }


def _handle_mob_move(farm: Farm, payload: dict) -> dict:
    mob = _get_active_mob_for_farm(farm, str(payload.get("mob_id") or "").strip())
    destination_farm_id = payload.get("destination_farm_id") or farm.id
    if str(destination_farm_id) != str(farm.id):
        _get_accessible_farm(str(destination_farm_id))
    session = MovementService.move_mob(
        mob=mob,
        allocations=payload.get("allocations") or [],
        destination_farm_id=str(destination_farm_id),
        when=_parse_iso_datetime(payload.get("event_time"), "event_time"),
    )
    db.session.flush()
    return {"grazing_session_id": str(session.id)}


def _handle_water_asset_status_update(farm: Farm, payload: dict) -> dict:
    asset_id = str(payload.get("water_asset_id") or payload.get("asset_id") or "").strip()
    asset = WaterAsset.query.filter_by(id=asset_id, farm_id=farm.id).first()
    if asset is None:
        raise MobileApiError("not_found", "Water asset not found for this farm", 404)

    allowed_fields = {"active", "status", "water_level"}
    update_payload = {field: payload[field] for field in allowed_fields if field in payload}
    if not update_payload:
        raise ValueError("At least one of active, status, or water_level is required")
    WaterNetworkService.update_asset(asset, update_payload)
    db.session.flush()
    network_state = WaterNetworkService.network_state_for_farm(str(farm.id))
    return {
        "water_asset": WaterNetworkService.serialize_asset(asset, network_state=network_state)
    }
