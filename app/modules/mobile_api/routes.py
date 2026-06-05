import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    CalendarActivity,
    Farm,
    GrazingSession,
    MobileAuthToken,
    MobileSyncCommand,
    Mob,
    MobEvent,
    NoteAttachment,
    Paddock,
    PaddockEvent,
    RainfallRecord,
    Task,
    TaskAttachment,
    TaskComment,
    TaskEntityLink,
    TaskSpace,
    User,
    WaterAsset,
    WaterAssetEvent,
    WaterConnection,
)
from app.models.stock_ledger import StockEventType
from app.modules.farms.map_routes import _build_farm_map_feature_collection
from app.services.calendar_service import CalendarService
from app.services.mob_event_service import MobEventService
from app.services.movement_service import MovementService
from app.services.note_attachment_service import NoteAttachmentService
from app.services.paddock_event_service import PaddockEventService
from app.services.stock_service import StockService
from app.services.task_service import (
    TASK_PRIORITIES,
    TASK_PRIORITY_LABELS,
    TASK_STATUSES,
    TASK_STATUS_LABELS,
    TaskService,
)
from app.services.water_network_service import WaterNetworkService
from app.services.water_asset_event_service import WaterAssetEventService

bp = Blueprint("mobile_api", __name__)

TOKEN_TTL_DAYS = 90
MAX_COMMANDS_PER_REQUEST = 100
STALE_RAINFALL_DAYS = 60
MAX_CONTINUOUS_GRAZING_DAYS = 10
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
ATTACHMENT_ROOT = "task_attachments"
ALLOWED_ATTACHMENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


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


def _serialize_farm(farm: Farm, role: str | None = None) -> dict:
    payload = {
        "id": str(farm.id),
        "name": farm.name,
        "timezone": farm.timezone,
        "active": farm.active,
        "default_stocking_rate_ha_per_lsu": float(farm.default_stocking_rate_ha_per_lsu),
        "updated_at": _iso_datetime(farm.updated_at),
    }
    if role:
        payload["role"] = role
    return payload


def _accessible_farm_summaries(user: User) -> list[dict]:
    roles = [
        role
        for role in user.farm_roles
        if role.farm is not None and role.farm.active
    ]
    return [
        _serialize_farm(role.farm, role=role.role)
        for role in sorted(roles, key=lambda item: item.farm.name.lower())
    ]


def _serialize_paddock(paddock: Paddock) -> dict:
    return {
        "id": str(paddock.id),
        "farm_id": str(paddock.farm_id),
        "name": paddock.name,
        "area_ha": float(paddock.area_ha or 0),
        "grazeable_area_ha": float(paddock.grazeable_area_ha or 0),
        "status": paddock.status,
        "notes": paddock.notes,
        "tags": TaskService.tags_from_csv(paddock.tags_csv),
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


def _option(value: str) -> dict:
    return {"value": value, "label": value.replace("_", " ").title()}


def _stock_form_options() -> dict:
    species_options = [
        {"value": species, "label": species}
        for species in StockService.SPECIES_MAP.values()
    ]
    return {
        "species_options": species_options,
        "sex_options_by_species": {
            species: [_option(option) for option in options]
            for species, options in StockService.SEX_OPTIONS.items()
        },
        "age_class_options_by_species": {
            species: [_option(option) for option in options]
            for species, options in StockService.AGE_CLASS_OPTIONS.items()
        },
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
    attachments = sorted(event.attachments, key=lambda item: item.created_at, reverse=True)
    return {
        "id": str(event.id),
        "farm_id": str(event.farm_id),
        "mob_id": str(event.mob_id),
        "event_at": _iso_datetime(event.event_at),
        "tags": MobEventService.tags_from_csv(event.tags_csv),
        "description": event.description,
        "attachment_count": len(attachments),
        "attachments": [_serialize_note_attachment(attachment) for attachment in attachments[:12]],
        "updated_at": _iso_datetime(event.updated_at),
    }


def _serialize_paddock_event(event: PaddockEvent) -> dict:
    attachments = sorted(event.attachments, key=lambda item: item.created_at, reverse=True)
    return {
        "id": str(event.id),
        "farm_id": str(event.farm_id),
        "paddock_id": str(event.paddock_id),
        "event_at": _iso_datetime(event.event_at),
        "tags": PaddockEventService.tags_from_csv(event.tags_csv),
        "description": event.description,
        "attachment_count": len(attachments),
        "attachments": [_serialize_note_attachment(attachment) for attachment in attachments[:12]],
        "updated_at": _iso_datetime(event.updated_at),
    }


def _serialize_water_asset_event(event: WaterAssetEvent) -> dict:
    attachments = sorted(event.attachments, key=lambda item: item.created_at, reverse=True)
    return {
        "id": str(event.id),
        "farm_id": str(event.farm_id),
        "water_asset_id": str(event.water_asset_id),
        "event_at": _iso_datetime(event.event_at),
        "tags": WaterAssetEventService.tags_from_csv(event.tags_csv),
        "description": event.description,
        "attachment_count": len(attachments),
        "attachments": [_serialize_note_attachment(attachment) for attachment in attachments[:12]],
        "updated_at": _iso_datetime(event.updated_at),
    }


def _serialize_task_space(space: TaskSpace) -> dict:
    return {
        "id": str(space.id),
        "farm_id": str(space.farm_id),
        "key": space.key,
        "name": space.name,
        "description": space.description,
        "updated_at": _iso_datetime(space.updated_at),
    }


def _serialize_task_entity_link(link: TaskEntityLink) -> dict:
    entity_type = "paddock"
    entity_id = link.paddock_id
    entity_name = link.paddock.name if link.paddock else None
    if link.water_asset_id:
        entity_type = "water_asset"
        entity_id = link.water_asset_id
        entity_name = link.water_asset.name if link.water_asset else None
    if link.mob_id:
        entity_type = "mob"
        entity_id = link.mob_id
        entity_name = link.mob.name if link.mob else None
    return {
        "id": str(link.id),
        "task_id": str(link.task_id),
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "entity_name": entity_name,
    }


def _serialize_task_attachment(attachment: TaskAttachment) -> dict:
    return {
        "id": str(attachment.id),
        "task_id": str(attachment.task_id),
        "client_attachment_id": attachment.client_attachment_id,
        "original_filename": attachment.original_filename,
        "content_type": attachment.content_type,
        "byte_size": attachment.byte_size,
        "sha256": attachment.sha256,
        "caption": attachment.caption,
        "captured_at": _iso_datetime(attachment.captured_at),
        "created_at": _iso_datetime(attachment.created_at),
    }


def _serialize_note_attachment(attachment: NoteAttachment) -> dict:
    return {
        "id": str(attachment.id),
        "farm_id": str(attachment.farm_id),
        "event_type": NoteAttachmentService.event_type_for_attachment(attachment),
        "event_id": str(
            attachment.mob_event_id
            or attachment.paddock_event_id
            or attachment.water_asset_event_id
        ),
        "client_attachment_id": attachment.client_attachment_id,
        "original_filename": attachment.original_filename,
        "content_type": attachment.content_type,
        "byte_size": attachment.byte_size,
        "sha256": attachment.sha256,
        "caption": attachment.caption,
        "captured_at": _iso_datetime(attachment.captured_at),
        "created_at": _iso_datetime(attachment.created_at),
    }


def _mobile_visible_entity_links(
    links: list[TaskEntityLink],
    *,
    active_mob_ids: set[str] | None = None,
) -> list[TaskEntityLink]:
    if active_mob_ids is None:
        return list(links)
    return [
        link
        for link in links
        if not link.mob_id or str(link.mob_id) in active_mob_ids
    ]


def _serialize_task(
    task: Task,
    *,
    active_mob_ids: set[str] | None = None,
) -> dict:
    attachments = sorted(task.attachments, key=lambda item: item.created_at, reverse=True)
    entity_links = _mobile_visible_entity_links(
        task.entity_links,
        active_mob_ids=active_mob_ids,
    )
    return {
        "id": str(task.id),
        "space_id": str(task.space_id),
        "farm_id": str(task.space.farm_id) if task.space else None,
        "display_key": task.display_key,
        "heading": task.heading,
        "description": task.description,
        "tags": TaskService.tags_from_csv(task.tags_csv),
        "reporter_name": task.reporter_name,
        "assignee_name": task.assignee_name,
        "status": task.status,
        "status_label": TASK_STATUS_LABELS.get(task.status, task.status),
        "priority": task.priority,
        "priority_label": TASK_PRIORITY_LABELS.get(task.priority, task.priority),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "started_at": _iso_datetime(task.started_at),
        "closed_at": _iso_datetime(task.closed_at),
        "updated_at": _iso_datetime(task.updated_at),
        "entity_links": [_serialize_task_entity_link(link) for link in entity_links],
        "comment_count": len(task.comments),
        "attachment_count": len(attachments),
        "attachments": [_serialize_task_attachment(attachment) for attachment in attachments[:12]],
    }


def _serialize_calendar_activity(activity: CalendarActivity) -> dict:
    return {
        "id": str(activity.id),
        "farm_id": str(activity.farm_id),
        "title": activity.title,
        "description": activity.description,
        "start_date": activity.start_date.isoformat(),
        "duration_days": float(activity.duration_days),
        "repeat_interval": activity.repeat_interval,
        "repeat_unit": activity.repeat_unit,
        "repeat_until": activity.repeat_until.isoformat() if activity.repeat_until else None,
        "updated_at": _iso_datetime(activity.updated_at),
    }


def _mobile_visible_calendar_entity_links(
    links: list[dict],
    *,
    active_mob_ids: set[str] | None = None,
) -> list[dict]:
    if active_mob_ids is None:
        return list(links)
    return [
        link
        for link in links
        if link.get("entity_type") != "mob" or str(link.get("entity_id")) in active_mob_ids
    ]


def _mobile_calendar_items(
    *,
    farm_id: str,
    range_start: date,
    range_end: date,
    active_mob_ids: set[str] | None = None,
) -> list[dict]:
    calendar_data = CalendarService.build_calendar_data(
        range_start=range_start,
        range_end=range_end,
        farm_id=farm_id,
    )
    items = []
    for day, day_items in calendar_data["items_by_date"].items():
        for item in day_items:
            entity_links = _mobile_visible_calendar_entity_links(
                item.get("entity_links") or [],
                active_mob_ids=active_mob_ids,
            )
            items.append(
                {
                    "kind": item["kind"],
                    "date": day.isoformat(),
                    "source_id": item.get("source_id"),
                    "task_id": item.get("task_id"),
                    "activity_id": item.get("activity_id"),
                    "title": item["title"],
                    "description": item.get("description"),
                    "subtitle": item.get("subtitle"),
                    "badge_text": item.get("badge_text"),
                    "farm_name": item.get("farm_name"),
                    "duration_text": item.get("duration_text"),
                    "stage": item.get("stage"),
                    "stage_label": item.get("stage_label"),
                    "assignee_name": item.get("assignee_name"),
                    "tags": item.get("tags") or [],
                    "priority": item.get("priority"),
                    "priority_label": item.get("priority_label"),
                    "entity_links": entity_links,
                    "occurrence_date": item.get("occurrence_date"),
                    "original_date": item.get("original_date"),
                    "is_moved": item.get("is_moved"),
                    "recurrence_text": item.get("recurrence_text"),
                }
            )
    return sorted(items, key=lambda item: (item["date"], item["kind"], item["title"].lower()))


def _active_grazing_by_paddock(active_grazing: list[GrazingSession]) -> dict[str, dict]:
    by_paddock: dict[str, dict] = {}
    for session in active_grazing:
        mob = session.mob
        if mob is None or mob.status != "active":
            continue
        for allocation in session.allocations:
            paddock_id = str(allocation.paddock_id)
            row = by_paddock.setdefault(
                paddock_id,
                {
                    "paddock_id": paddock_id,
                    "mobs": [],
                    "species_heads": {},
                    "group_heads": {},
                    "total_head": 0.0,
                },
            )
            fraction = float(allocation.allocation_fraction)
            row["mobs"].append(
                {
                    "mob_id": str(mob.id),
                    "mob_name": mob.name,
                    "start_at": _iso_datetime(session.start_at),
                    "allocation_fraction": fraction,
                    "allocation_pct": round(fraction * 100.0, 2),
                }
            )
            for balance in mob.balances:
                species = balance.animal_group_type.species
                head = float(balance.head_count) * fraction
                row["species_heads"][species] = row["species_heads"].get(species, 0.0) + head
                group_id = str(balance.animal_group_type_id)
                group_row = row["group_heads"].setdefault(
                    group_id,
                    {
                        "animal_group_type_id": group_id,
                        "animal_group_type": _serialize_animal_group_type(balance.animal_group_type),
                        "head": 0.0,
                    },
                )
                group_row["head"] += head
                row["total_head"] += head
    for row in by_paddock.values():
        row["species_heads"] = [
            {"species": species, "head": round(head, 2)}
            for species, head in sorted(row["species_heads"].items())
        ]
        row["group_heads"] = [
            {
                **group_row,
                "head": round(group_row["head"], 2),
            }
            for group_row in sorted(
                row["group_heads"].values(),
                key=lambda item: (
                    item["animal_group_type"]["species"],
                    item["animal_group_type"]["breed"],
                    item["animal_group_type"]["sex"],
                    item["animal_group_type"]["age_class"],
                ),
            )
        ]
        row["total_head"] = round(row["total_head"], 2)
        row["mobs"].sort(key=lambda item: item["mob_name"].lower())
    return by_paddock


def _decision_feed(
    *,
    farm: Farm,
    tasks: list[Task],
    rainfall: list[RainfallRecord],
    active_grazing: list[GrazingSession],
    water_assets: list[WaterAsset],
    network_state: dict,
) -> list[dict]:
    today = date.today()
    items = []

    for task in tasks:
        if task.due_date and task.due_date < today and task.status != "closed":
            items.append(
                {
                    "severity": "high",
                    "category": "task",
                    "title": f"Overdue task: {task.display_key}",
                    "detail": task.heading,
                    "entity_type": "task",
                    "entity_id": str(task.id),
                }
            )

    for paddock_id, alert in (network_state.get("paddock_alerts") or {}).items():
        items.append(
            {
                "severity": "high" if alert.get("level") == "critical" else "medium",
                "category": "water",
                "title": "Paddock water risk",
                "detail": alert.get("message") or "Water network needs attention.",
                "entity_type": "paddock",
                "entity_id": paddock_id,
            }
        )

    for asset in water_assets:
        level = (asset.water_level or "").strip().lower()
        status = (asset.status or "").strip().lower()
        level_requires_attention = asset.asset_type != "weir" and level in {"empty", "low"}
        status_requires_attention = status in {"dry", "blocked", "broken", "offline"}
        if level_requires_attention or status_requires_attention:
            items.append(
                {
                    "severity": "high" if level_requires_attention and level == "empty" else "medium",
                    "category": "water",
                    "title": f"Check {asset.name}",
                    "detail": f"{asset.asset_type} status {asset.status or 'unknown'}, level {asset.water_level or 'unknown'}",
                    "entity_type": "water_asset",
                    "entity_id": str(asset.id),
                }
            )

    for session in sorted(active_grazing, key=lambda item: item.start_at):
        mob = session.mob
        if mob is None or mob.status != "active":
            continue
        grazing_days = (today - session.start_at.date()).days
        if grazing_days < MAX_CONTINUOUS_GRAZING_DAYS:
            continue
        for allocation in sorted(
            session.allocations,
            key=lambda item: (item.paddock.name if item.paddock else "").lower(),
        ):
            paddock = allocation.paddock
            if paddock is None:
                continue
            allocation_pct = round(float(allocation.allocation_fraction) * 100.0, 2)
            items.append(
                {
                    "severity": "high",
                    "category": "grazing",
                    "title": f"Move {mob.name} off {paddock.name}",
                    "detail": (
                        f"{mob.name} has been grazing {paddock.name} for {grazing_days} days "
                        f"continuously (limit {MAX_CONTINUOUS_GRAZING_DAYS} days, "
                        f"{allocation_pct:g}% allocation). Move the mob off this paddock."
                    ),
                    "entity_type": "mob",
                    "entity_id": str(mob.id),
                }
            )

    latest_rain = rainfall[0].recorded_on if rainfall else None
    if latest_rain is None:
        items.append(
            {
                "severity": "medium",
                "category": "rainfall",
                "title": "No recent rainfall records",
                "detail": f"Record rain for {farm.name} when available.",
                "entity_type": "farm",
                "entity_id": str(farm.id),
            }
        )
    elif (today - latest_rain).days >= STALE_RAINFALL_DAYS:
        items.append(
            {
                "severity": "medium",
                "category": "rainfall",
                "title": "Rainfall record is stale",
                "detail": f"Last rain was recorded on {latest_rain.isoformat()}.",
                "entity_type": "farm",
                "entity_id": str(farm.id),
            }
        )

    active_mobs = [mob for mob in farm.mobs if mob.status == "active"]
    for mob in active_mobs:
        if not any(int(balance.head_count) > 0 for balance in mob.balances):
            items.append(
                {
                    "severity": "medium",
                    "category": "stock",
                    "title": f"Count {mob.name}",
                    "detail": "Active mob has no current head count in the snapshot.",
                    "entity_type": "mob",
                    "entity_id": str(mob.id),
                }
            )

    return items[:40]


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
            "farms": _accessible_farm_summaries(user),
        }
    )


@bp.post("/auth/logout")
def logout():
    g.mobile_token.revoked_at = _utcnow()
    db.session.commit()
    return jsonify({"status": "ok"})


@bp.get("/ping")
def ping():
    return jsonify(
        {
            "status": "ok",
            "server_time": _iso_datetime(_utcnow()),
            "user": _serialize_user(g.mobile_user),
            "farms": _accessible_farm_summaries(g.mobile_user),
        }
    )


@bp.get("/bootstrap")
def bootstrap():
    user = g.mobile_user
    return jsonify(
        {
            "server_time": _iso_datetime(_utcnow()),
            "user": _serialize_user(user),
            "farms": _accessible_farm_summaries(user),
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
                    "mob.create",
                    "mob_event.create",
                    "paddock_event.create",
                    "water_asset_event.create",
                    "stock_count.record",
                    "mob.move",
                    "mob.transfer",
                    "task.create",
                    "task.status.update",
                    "task.comment.create",
                    "paddock.update",
                    "water_asset_status.update",
                ],
                "max_commands_per_request": MAX_COMMANDS_PER_REQUEST,
            },
            "form_options": {
                **_stock_form_options(),
                "task_statuses": [
                    {"value": status, "label": TASK_STATUS_LABELS[status]} for status in TASK_STATUSES
                ],
                "task_priorities": [
                    {"value": priority, "label": TASK_PRIORITY_LABELS[priority]}
                    for priority in TASK_PRIORITIES
                ],
                "water_status_options_by_type": {
                    asset_type: [
                        {"value": option, "label": option.replace("_", " ").title()}
                        for option in sorted(options)
                    ]
                    for asset_type, options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.items()
                },
                "water_level_asset_types": sorted(WaterNetworkService.WATER_LEVEL_TYPES),
                "water_level_options": [
                    {"value": option, "label": option.replace("_", " ").title()}
                    for option in WaterNetworkService.WATER_LEVEL_OPTIONS
                ],
            },
        }
    )


@bp.get("/farms/<farm_id>/snapshot")
def farm_snapshot(farm_id):
    farm = _get_accessible_farm(farm_id)
    today = date.today()
    calendar_range_end = today + timedelta(days=90)
    paddocks = Paddock.query.filter_by(farm_id=farm.id).order_by(Paddock.name.asc()).all()
    mobs = Mob.query.filter_by(farm_id=farm.id, status="active").order_by(Mob.name.asc()).all()
    active_mob_ids = {str(mob.id) for mob in mobs}
    active_grazing = (
        GrazingSession.query.join(Mob)
        .filter(
            GrazingSession.farm_id == farm.id,
            GrazingSession.end_at.is_(None),
            Mob.farm_id == farm.id,
            Mob.status == "active",
        )
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
        MobEvent.query.join(Mob)
        .filter(
            MobEvent.farm_id == farm.id,
            Mob.farm_id == farm.id,
            Mob.status == "active",
        )
        .order_by(MobEvent.event_at.desc(), MobEvent.created_at.desc())
        .limit(200)
        .all()
    )
    paddock_events = (
        PaddockEvent.query.filter_by(farm_id=farm.id)
        .order_by(PaddockEvent.event_at.desc(), PaddockEvent.created_at.desc())
        .limit(200)
        .all()
    )
    water_asset_events = (
        WaterAssetEvent.query.filter_by(farm_id=farm.id)
        .order_by(WaterAssetEvent.event_at.desc(), WaterAssetEvent.created_at.desc())
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
    task_spaces = TaskSpace.query.filter_by(farm_id=farm.id).order_by(TaskSpace.key.asc()).all()
    tasks = (
        Task.query.join(TaskSpace)
        .filter(TaskSpace.farm_id == farm.id, Task.status != "closed")
        .order_by(Task.due_date.asc(), Task.priority.desc(), Task.task_number.asc())
        .all()
    )
    calendar_activities = (
        CalendarActivity.query.filter_by(farm_id=farm.id)
        .filter(
            or_(CalendarActivity.repeat_until.is_(None), CalendarActivity.repeat_until >= today),
            CalendarActivity.start_date <= calendar_range_end,
        )
        .order_by(CalendarActivity.start_date.asc(), CalendarActivity.title.asc())
        .all()
    )
    network_state = WaterNetworkService.network_state_for_farm(str(farm.id))
    active_by_paddock = _active_grazing_by_paddock(active_grazing)
    try:
        mobile_map = _build_farm_map_feature_collection(
            farm,
            include_water=True,
            allow_missing_kml=True,
        )
    except Exception as exc:
        mobile_map = {"features": [], "warnings": [str(exc)]}

    return jsonify(
        {
            "server_time": _iso_datetime(_utcnow()),
            "farm": _serialize_farm(farm),
            "paddocks": [_serialize_paddock(paddock) for paddock in paddocks],
            "mobs": [_serialize_mob(mob) for mob in mobs],
            "active_grazing": [
                _serialize_grazing_session(session) for session in active_grazing
            ],
            "active_grazing_by_paddock": list(active_by_paddock.values()),
            "rainfall": [_serialize_rainfall(record) for record in rainfall],
            "mob_events": [_serialize_mob_event(event) for event in mob_events],
            "paddock_events": [_serialize_paddock_event(event) for event in paddock_events],
            "water_asset_events": [
                _serialize_water_asset_event(event) for event in water_asset_events
            ],
            "water_assets": [
                WaterNetworkService.serialize_asset(asset, network_state=network_state)
                for asset in water_assets
            ],
            "water_connections": [
                WaterNetworkService.serialize_connection(connection)
                for connection in water_connections
            ],
            "task_spaces": [_serialize_task_space(space) for space in task_spaces],
            "tasks": [_serialize_task(task, active_mob_ids=active_mob_ids) for task in tasks],
            "calendar_activities": [
                _serialize_calendar_activity(activity) for activity in calendar_activities
            ],
            "calendar_items": _mobile_calendar_items(
                farm_id=str(farm.id),
                range_start=today,
                range_end=calendar_range_end,
                active_mob_ids=active_mob_ids,
            ),
            "decision_feed": _decision_feed(
                farm=farm,
                tasks=tasks,
                rainfall=rainfall,
                active_grazing=active_grazing,
                water_assets=water_assets,
                network_state=network_state,
            ),
            "map_features": mobile_map.get("features", []),
            "map_warnings": mobile_map.get("warnings", []),
        }
    )


@bp.get("/farms/<farm_id>/map-data")
def farm_map_data(farm_id):
    farm = _get_accessible_farm(farm_id)
    try:
        return jsonify(
            _build_farm_map_feature_collection(
                farm,
                include_water=True,
                allow_missing_kml=True,
            )
        )
    except Exception as exc:
        raise MobileApiError("map_unavailable", str(exc), 500) from exc


@bp.get("/map-data")
def all_farms_map_data():
    features = []
    warnings = []
    farm_count = 0
    for role in g.mobile_user.farm_roles:
        if role.farm is None or not role.farm.active:
            continue
        farm_count += 1
        try:
            payload = _build_farm_map_feature_collection(
                role.farm,
                include_water=True,
                allow_missing_kml=True,
            )
        except Exception as exc:
            warnings.append(f"{role.farm.name}: {exc}")
            continue
        features.extend(payload.get("features", []))
        warnings.extend(payload.get("warnings", []))
    return jsonify(
        {
            "type": "FeatureCollection",
            "farm_count": farm_count,
            "generated_at": _iso_datetime(_utcnow()),
            "warnings": warnings,
            "features": features,
        }
    )


def _attachment_storage_path(*, farm_id: str, task_id: str, attachment_id: str, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}:
        suffix = ".jpg"
    return Path(ATTACHMENT_ROOT) / str(farm_id) / str(task_id) / f"{attachment_id}{suffix}"


def _absolute_attachment_path(relative_path: str | Path) -> Path:
    root = Path(current_app.instance_path).resolve()
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise MobileApiError("invalid_attachment", "Attachment storage path is invalid", 500)
    return target


def _store_task_attachment(farm: Farm, task: Task) -> tuple[TaskAttachment, bool]:
    client_attachment_id = str(request.form.get("client_attachment_id") or "").strip()
    if not client_attachment_id:
        raise MobileApiError("invalid_payload", "client_attachment_id is required")

    existing = TaskAttachment.query.filter_by(
        uploaded_by_user_id=g.mobile_user.id,
        client_attachment_id=client_attachment_id,
    ).first()
    if existing is not None:
        if str(existing.task_id) != str(task.id):
            raise MobileApiError("conflict", "client_attachment_id already belongs to another task", 409)
        return existing, True

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise MobileApiError("invalid_payload", "file is required")

    original_filename = secure_filename(uploaded.filename) or "mobile-photo.jpg"
    content_type = (uploaded.mimetype or "application/octet-stream").lower()
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        raise MobileApiError("invalid_payload", "Only image attachments are supported")

    data = uploaded.read()
    if not data:
        raise MobileApiError("invalid_payload", "Attachment file is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise MobileApiError("invalid_payload", "Attachment file is too large")

    digest = hashlib.sha256(data).hexdigest()
    requested_digest = str(request.form.get("sha256") or "").strip().lower()
    if requested_digest and requested_digest != digest:
        raise MobileApiError("invalid_payload", "Attachment checksum does not match")

    attachment = TaskAttachment(
        task_id=task.id,
        uploaded_by_user_id=g.mobile_user.id,
        client_attachment_id=client_attachment_id,
        original_filename=original_filename[:255],
        content_type=content_type,
        byte_size=len(data),
        sha256=digest,
        caption=TaskService.optional_text(
            request.form.get("caption"),
            TaskService.MAX_DESCRIPTION_LENGTH,
        ),
        captured_at=_parse_iso_datetime(request.form.get("captured_at"), "captured_at"),
        storage_path="",
    )
    db.session.add(attachment)
    db.session.flush()

    relative_path = _attachment_storage_path(
        farm_id=str(farm.id),
        task_id=str(task.id),
        attachment_id=str(attachment.id),
        filename=original_filename,
    )
    target = _absolute_attachment_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    attachment.storage_path = relative_path.as_posix()
    db.session.flush()
    return attachment, False


@bp.post("/farms/<farm_id>/tasks/<task_id>/attachments")
def upload_task_attachment(farm_id, task_id):
    farm = _get_accessible_farm(farm_id)
    task = _get_task_for_farm(farm, str(task_id).strip())
    try:
        attachment, duplicate = _store_task_attachment(farm, task)
        db.session.commit()
        return jsonify({"attachment": _serialize_task_attachment(attachment), "duplicate": duplicate})
    except MobileApiError:
        db.session.rollback()
        raise
    except ValueError as exc:
        db.session.rollback()
        raise MobileApiError("invalid_payload", str(exc), 400) from exc


@bp.get("/farms/<farm_id>/tasks/<task_id>/attachments/<attachment_id>")
def task_attachment_file(farm_id, task_id, attachment_id):
    farm = _get_accessible_farm(farm_id)
    _get_task_for_farm(farm, str(task_id).strip())
    attachment = TaskAttachment.query.filter_by(id=attachment_id, task_id=task_id).first()
    if attachment is None:
        raise MobileApiError("not_found", "Attachment not found", 404)
    target = _absolute_attachment_path(attachment.storage_path)
    if not target.exists():
        raise MobileApiError("not_found", "Attachment file not found", 404)
    return send_file(
        target,
        mimetype=attachment.content_type,
        as_attachment=False,
        download_name=attachment.original_filename,
    )


def _get_note_event_for_farm(farm: Farm, event_type: str, event_id: str):
    normalized_id = str(event_id or "").strip()
    if event_type == "mob_event":
        event = MobEvent.query.filter_by(id=normalized_id, farm_id=farm.id).first()
        message = "Mob note not found for this farm"
    elif event_type == "paddock_event":
        event = PaddockEvent.query.filter_by(id=normalized_id, farm_id=farm.id).first()
        message = "Paddock note not found for this farm"
    elif event_type == "water_asset_event":
        event = WaterAssetEvent.query.filter_by(id=normalized_id, farm_id=farm.id).first()
        message = "Water asset note not found for this farm"
    else:
        raise MobileApiError("invalid_payload", "Attachment event type is invalid")
    if event is None:
        raise MobileApiError("not_found", message, 404)
    return event


def _store_note_attachment(farm: Farm, event_type: str, event_id: str) -> tuple[NoteAttachment, bool]:
    event = _get_note_event_for_farm(farm, event_type, event_id)
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise MobileApiError("invalid_payload", "file is required")
    try:
        return NoteAttachmentService.store_upload(
            uploaded,
            farm_id=str(farm.id),
            event_type=event_type,
            event_id=str(event.id),
            instance_path=current_app.instance_path,
            uploaded_by_user_id=str(g.mobile_user.id),
            client_attachment_id=request.form.get("client_attachment_id"),
            caption=request.form.get("caption"),
            captured_at=_parse_iso_datetime(request.form.get("captured_at"), "captured_at"),
            expected_sha256=request.form.get("sha256"),
        )
    except ValueError as exc:
        raise MobileApiError("invalid_payload", str(exc), 400) from exc


def _upload_note_attachment_response(farm_id: str, event_type: str, event_id: str):
    farm = _get_accessible_farm(farm_id)
    try:
        attachment, duplicate = _store_note_attachment(farm, event_type, event_id)
        db.session.commit()
        return jsonify({"attachment": _serialize_note_attachment(attachment), "duplicate": duplicate})
    except MobileApiError:
        db.session.rollback()
        raise


@bp.post("/farms/<farm_id>/mob-events/<event_id>/attachments")
def upload_mob_event_attachment(farm_id, event_id):
    return _upload_note_attachment_response(farm_id, "mob_event", event_id)


@bp.post("/farms/<farm_id>/paddock-events/<event_id>/attachments")
def upload_paddock_event_attachment(farm_id, event_id):
    return _upload_note_attachment_response(farm_id, "paddock_event", event_id)


@bp.post("/farms/<farm_id>/water-asset-events/<event_id>/attachments")
def upload_water_asset_event_attachment(farm_id, event_id):
    return _upload_note_attachment_response(farm_id, "water_asset_event", event_id)


@bp.get("/farms/<farm_id>/note-attachments/<attachment_id>")
def note_attachment_file(farm_id, attachment_id):
    _get_accessible_farm(farm_id)
    attachment = NoteAttachment.query.filter_by(id=attachment_id, farm_id=farm_id).first()
    if attachment is None:
        raise MobileApiError("not_found", "Attachment not found", 404)
    try:
        target = NoteAttachmentService.absolute_attachment_path(
            current_app.instance_path,
            attachment.storage_path,
        )
    except FileNotFoundError as exc:
        raise MobileApiError("not_found", "Attachment file not found", 404) from exc
    if not target.exists():
        raise MobileApiError("not_found", "Attachment file not found", 404)
    return send_file(
        target,
        mimetype=attachment.content_type,
        as_attachment=False,
        download_name=attachment.original_filename,
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
        "mob.create": _handle_mob_create,
        "mob_event.create": _handle_mob_event_create,
        "paddock_event.create": _handle_paddock_event_create,
        "water_asset_event.create": _handle_water_asset_event_create,
        "stock_count.record": _handle_stock_count_record,
        "mob.move": _handle_mob_move,
        "mob.transfer": _handle_mob_transfer,
        "task.create": _handle_task_create,
        "task.status.update": _handle_task_status_update,
        "task.comment.create": _handle_task_comment_create,
        "paddock.update": _handle_paddock_update,
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


def _handle_mob_create(farm: Farm, payload: dict) -> dict:
    mob = Mob(
        farm_id=farm.id,
        name=TaskService.require_text(payload.get("name"), "Mob name", TaskService.MAX_NAME_LENGTH),
        status="active",
        origin_note=TaskService.optional_text(
            payload.get("origin_note") or payload.get("note"),
            TaskService.MAX_DESCRIPTION_LENGTH,
        ),
    )
    db.session.add(mob)
    try:
        db.session.flush()
    except IntegrityError as exc:
        raise MobileApiError("invalid_command", "Active mob name already exists on this farm") from exc
    return {"mob": _serialize_mob(mob)}


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


def _handle_paddock_event_create(farm: Farm, payload: dict) -> dict:
    paddock_id = str(payload.get("paddock_id") or "").strip()
    paddock = Paddock.query.filter_by(id=paddock_id, farm_id=farm.id).first()
    if paddock is None:
        raise MobileApiError("not_found", "Paddock not found for this farm", 404)
    raw_tags = payload.get("tags")
    if isinstance(raw_tags, list):
        raw_tags = ",".join(str(value) for value in raw_tags)
    event = PaddockEventService.create_event(
        paddock_id=paddock.id,
        farm_id=farm.id,
        description=payload.get("description"),
        raw_tags=raw_tags,
        event_at=_parse_iso_datetime(payload.get("event_at"), "event_at"),
    )
    db.session.flush()
    return {"event_id": str(event.id)}


def _handle_water_asset_event_create(farm: Farm, payload: dict) -> dict:
    asset_id = str(payload.get("water_asset_id") or payload.get("asset_id") or "").strip()
    asset = WaterAsset.query.filter_by(id=asset_id, farm_id=farm.id).first()
    if asset is None:
        raise MobileApiError("not_found", "Water asset not found for this farm", 404)
    raw_tags = payload.get("tags")
    if isinstance(raw_tags, list):
        raw_tags = ",".join(str(value) for value in raw_tags)
    event = WaterAssetEventService.create_event(
        water_asset_id=asset.id,
        farm_id=farm.id,
        description=payload.get("description"),
        raw_tags=raw_tags,
        event_at=_parse_iso_datetime(payload.get("event_at"), "event_at"),
    )
    db.session.flush()
    return {"event_id": str(event.id)}


def _resolve_stock_count_group(payload: dict) -> tuple[AnimalGroupType, bool]:
    group_id = str(payload.get("animal_group_type_id") or "").strip()
    if group_id:
        group_type = db.session.get(AnimalGroupType, group_id)
        if group_type is None:
            raise ValueError("animal_group_type_id is invalid")
        return group_type, False

    group_payload = payload.get("animal_group_type")
    if not isinstance(group_payload, dict):
        raise ValueError("animal_group_type_id or animal_group_type is required")

    breed = " ".join(str(group_payload.get("breed") or "").strip().split())
    if not breed:
        raise ValueError("breed is required")

    return (
        StockService.get_or_create_group_type(
            species=str(group_payload.get("species") or "").strip(),
            breed=breed,
            sex=str(group_payload.get("sex") or "").strip(),
            age_class=str(group_payload.get("age_class") or "").strip(),
        ),
        True,
    )


def _handle_stock_count_record(farm: Farm, payload: dict) -> dict:
    mob = _get_active_mob_for_farm(farm, str(payload.get("mob_id") or "").strip())
    group_type, uses_group_payload = _resolve_stock_count_group(payload)
    group_id = str(group_type.id)
    try:
        counted_quantity = int(payload.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be a whole number") from exc
    if counted_quantity < 0:
        raise ValueError("quantity must be greater than or equal to 0")
    if uses_group_payload and counted_quantity <= 0:
        raise ValueError("quantity must be greater than 0 for a new animal group")

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
        "animal_group_type_id": group_id,
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


def _handle_mob_transfer(farm: Farm, payload: dict) -> dict:
    source = _get_active_mob_for_farm(
        farm,
        str(payload.get("source_mob_id") or payload.get("mob_id") or "").strip(),
    )
    destination_mob_id = str(payload.get("destination_mob_id") or "").strip()
    if not destination_mob_id:
        raise ValueError("destination_mob_id is required")
    destination = Mob.query.filter_by(id=destination_mob_id, status="active").first()
    if destination is None:
        raise MobileApiError("not_found", "Destination mob not found", 404)
    destination_farm_id = str(payload.get("destination_farm_id") or destination.farm_id)
    _get_accessible_farm(destination_farm_id)
    MovementService.transfer_stock_between_mobs(
        source_mob=source,
        destination_mob=destination,
        transfers=payload.get("transfers") or [],
        destination_farm_id=destination_farm_id,
        note=payload.get("note"),
        when=_parse_iso_datetime(payload.get("event_time"), "event_time"),
    )
    db.session.flush()
    return {
        "source_mob_id": str(source.id),
        "destination_mob_id": str(destination.id),
        "status": "ok",
    }


def _get_mobile_task_space(farm: Farm, payload: dict) -> TaskSpace:
    space_id = str(payload.get("space_id") or "").strip()
    if space_id:
        space = TaskSpace.query.filter_by(id=space_id, farm_id=farm.id).first()
        if space is None:
            raise MobileApiError("not_found", "Task space not found for this farm", 404)
        return space

    key = f"FIELD-{str(farm.id)[:8].upper()}"
    space = TaskSpace.query.filter_by(key=key).first()
    if space is None:
        space = TaskSpace(
            farm_id=farm.id,
            key=key,
            name="Field Work",
            description="Mobile-created field tasks.",
        )
        db.session.add(space)
        db.session.flush()
    return space


def _get_task_for_farm(farm: Farm, task_id: str) -> Task:
    task = Task.query.join(TaskSpace).filter(Task.id == task_id, TaskSpace.farm_id == farm.id).first()
    if task is None:
        raise MobileApiError("not_found", "Task not found for this farm", 404)
    return task


def _payload_entity_ids(payload: dict, plural_key: str, singular_key: str) -> list[str]:
    values = payload.get(plural_key)
    if values is None and singular_key in payload:
        values = [payload.get(singular_key)]
    return TaskService.normalize_entity_ids(values)


def _handle_task_create(farm: Farm, payload: dict) -> dict:
    space = _get_mobile_task_space(farm, payload)
    heading = payload.get("heading") or payload.get("title")
    description = payload.get("description") or heading
    raw_tags = payload.get("tags")
    if isinstance(raw_tags, list):
        raw_tags = ",".join(str(value) for value in raw_tags)
    task = TaskService.create_task(
        space=space,
        heading=heading,
        description=description,
        raw_tags=raw_tags,
        reporter_name=payload.get("reporter_name") or g.mobile_user.name,
        assignee_name=payload.get("assignee_name"),
        status=payload.get("status") or "todo",
        priority=payload.get("priority") or "low",
        original_estimate_days=payload.get("original_estimate_days"),
        due_date=payload.get("due_date"),
    )
    TaskService.add_entity_links(
        task=task,
        paddock_ids=_payload_entity_ids(payload, "paddock_ids", "paddock_id"),
        water_asset_ids=_payload_entity_ids(payload, "water_asset_ids", "water_asset_id"),
        mob_ids=_payload_entity_ids(payload, "mob_ids", "mob_id"),
    )
    db.session.flush()
    return {"task": _serialize_task(task)}


def _handle_task_status_update(farm: Farm, payload: dict) -> dict:
    task_id = str(payload.get("task_id") or "").strip()
    task = _get_task_for_farm(farm, task_id)
    TaskService.apply_status_transition(
        task,
        payload.get("status"),
        payload.get("changed_by_name") or g.mobile_user.name,
        payload.get("note"),
        when=_parse_iso_datetime(payload.get("changed_at"), "changed_at"),
    )
    db.session.flush()
    return {"task": _serialize_task(task)}


def _handle_task_comment_create(farm: Farm, payload: dict) -> dict:
    task_id = str(payload.get("task_id") or "").strip()
    task = _get_task_for_farm(farm, task_id)
    comment = TaskComment(
        task_id=task.id,
        author_name=TaskService.require_text(
            payload.get("author_name") or g.mobile_user.name,
            "Author",
            TaskService.MAX_NAME_LENGTH,
        ),
        body=TaskService.require_text(
            payload.get("body") or payload.get("note"),
            "Comment",
            TaskService.MAX_DESCRIPTION_LENGTH,
        ),
    )
    db.session.add(comment)
    db.session.flush()
    return {"comment_id": str(comment.id), "task_id": str(task.id)}


def _handle_paddock_update(farm: Farm, payload: dict) -> dict:
    paddock_id = str(payload.get("paddock_id") or "").strip()
    paddock = Paddock.query.filter_by(id=paddock_id, farm_id=farm.id).first()
    if paddock is None:
        raise MobileApiError("not_found", "Paddock not found for this farm", 404)

    if "status" in payload:
        paddock.status = TaskService.require_text(payload.get("status"), "Paddock status", 20)
    if "notes" in payload:
        paddock.notes = TaskService.optional_text(payload.get("notes"), TaskService.MAX_DESCRIPTION_LENGTH)
    if "tags" in payload:
        raw_tags = payload.get("tags")
        if isinstance(raw_tags, list):
            raw_tags = ",".join(str(value) for value in raw_tags)
        paddock.tags_csv = TaskService.tags_to_csv(TaskService.parse_optional_tags(raw_tags))
    db.session.flush()
    return {"paddock": _serialize_paddock(paddock)}


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
