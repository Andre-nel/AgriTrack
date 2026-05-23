import re
from datetime import date, datetime, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func

from app.extensions import db
from app.models import Mob, Paddock, Task, TaskEntityLink, TaskLink, TaskSpace, TaskStatusTransition, WaterAsset

TASK_STATUSES = (
    "todo",
    "selected_for_execution",
    "in_progress",
    "impeded",
    "ready_for_verification",
    "verification_in_progress",
    "closed",
)
TASK_STATUS_LABELS = {
    "todo": "TO DO",
    "selected_for_execution": "Selected For Execution",
    "in_progress": "In Progress",
    "impeded": "Impeded",
    "ready_for_verification": "Ready For Verification",
    "verification_in_progress": "Verification In Progress",
    "closed": "Closed",
}
TASK_START_STATUSES = {"selected_for_execution", "in_progress"}
TASK_PRIORITIES = ("lowest", "low", "high", "highest")
TASK_PRIORITY_LABELS = {
    "lowest": "Lowest",
    "low": "Low",
    "high": "High",
    "highest": "Highest",
}
TASK_LINK_TYPES = ("relates_to", "blocks", "references")
TASK_LINK_TYPE_LABELS = {
    "relates_to": "Relates To",
    "blocks": "Blocks",
    "references": "References",
}


class TaskService:
    TAG_SPLIT_PATTERN = re.compile(r"[,;\n]+")
    MAX_TAGS = 20
    MAX_TAG_LENGTH = 40
    MAX_KEY_LENGTH = 20
    MAX_HEADING_LENGTH = 200
    MAX_NAME_LENGTH = 120
    MAX_DESCRIPTION_LENGTH = 5000

    @staticmethod
    def require_text(value: str | None, field_name: str, max_length: int | None = None) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if max_length is not None and len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def optional_text(value: str | None, max_length: int | None = None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if max_length is not None and len(normalized) > max_length:
            raise ValueError(f"Value must be {max_length} characters or fewer")
        return normalized

    @classmethod
    def normalize_space_key(cls, raw_value: str | None) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "-", (raw_value or "").upper()).strip("-")
        if not normalized:
            raise ValueError("Task space key is required")
        if len(normalized) > cls.MAX_KEY_LENGTH:
            raise ValueError(f"Task space key must be {cls.MAX_KEY_LENGTH} characters or fewer")
        return normalized

    @classmethod
    def validate_status(cls, raw_value: str | None, *, allow_closed: bool = True) -> str:
        value = " ".join((raw_value or "").strip().lower().split()).replace(" ", "_")
        if value not in TASK_STATUSES:
            raise ValueError("Task status is invalid")
        if not allow_closed and value == "closed":
            raise ValueError("New tasks cannot start in Closed")
        return value

    @staticmethod
    def validate_priority(raw_value: str | None) -> str:
        value = " ".join((raw_value or "").strip().lower().split()).replace(" ", "_")
        if value not in TASK_PRIORITIES:
            raise ValueError("Task priority is invalid")
        return value

    @staticmethod
    def validate_link_type(raw_value: str | None) -> str:
        value = " ".join((raw_value or "").strip().lower().split()).replace(" ", "_")
        if value not in TASK_LINK_TYPES:
            raise ValueError("Task link type is invalid")
        return value

    @staticmethod
    def _normalize_tag(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

    @classmethod
    def parse_optional_tags(cls, raw_tags: str | None) -> list[str]:
        tokens = cls.TAG_SPLIT_PATTERN.split(raw_tags or "")
        tags = []
        seen = set()
        for token in tokens:
            tag = cls._normalize_tag(token)
            if not tag:
                continue
            if len(tag) > cls.MAX_TAG_LENGTH:
                raise ValueError(f"Each tag must be {cls.MAX_TAG_LENGTH} characters or fewer")
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)

        if len(tags) > cls.MAX_TAGS:
            raise ValueError(f"No more than {cls.MAX_TAGS} tags are allowed")
        return tags

    @staticmethod
    def tags_to_csv(tags: list[str]) -> str:
        return ",".join(tags)

    @classmethod
    def tags_from_csv(cls, raw_csv: str | None) -> list[str]:
        return [tag for tag in (cls._normalize_tag(value) for value in (raw_csv or "").split(",")) if tag]

    @staticmethod
    def parse_optional_due_date(raw_value: str | None) -> date | None:
        text = (raw_value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("Due date must be valid (YYYY-MM-DD)") from exc

    @staticmethod
    def parse_optional_estimate(raw_value: str | None) -> Decimal | None:
        text = (raw_value or "").strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("Original estimate must be a valid number of days") from exc
        if value <= 0:
            raise ValueError("Original estimate must be greater than 0")
        return value

    @staticmethod
    def _ensure_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def get_timezone(tz_name: str | None) -> tzinfo:
        normalized = (tz_name or "UTC").strip() or "UTC"
        if normalized.upper() == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(normalized)
        except ZoneInfoNotFoundError:
            return timezone.utc

    @classmethod
    def localize_datetime(cls, value: datetime | None, tz_name: str | None) -> datetime | None:
        if value is None:
            return None
        return cls._ensure_aware(value).astimezone(cls.get_timezone(tz_name))

    @classmethod
    def format_local_datetime(cls, value: datetime | None, tz_name: str | None) -> str | None:
        localized = cls.localize_datetime(value, tz_name)
        if localized is None:
            return None
        return localized.strftime("%Y-%m-%d %H:%M")

    @classmethod
    def format_local_date(cls, value: datetime | None, tz_name: str | None) -> str | None:
        localized = cls.localize_datetime(value, tz_name)
        if localized is None:
            return None
        return localized.date().isoformat()

    @classmethod
    def today_for_timezone(cls, tz_name: str | None) -> date:
        return datetime.now(timezone.utc).astimezone(cls.get_timezone(tz_name)).date()

    @staticmethod
    def format_estimate(value: Decimal | None) -> str | None:
        if value is None:
            return None
        return f"{float(value):.2f} days"

    @staticmethod
    def format_duration(started_at: datetime | None, closed_at: datetime | None) -> str | None:
        if started_at is None or closed_at is None:
            return None
        seconds = max((closed_at - started_at).total_seconds(), 0)
        if seconds < 86400:
            return f"{seconds / 3600.0:.1f} hours"
        return f"{seconds / 86400.0:.1f} days"

    @staticmethod
    def next_task_number(space_id: str) -> int:
        current_max = db.session.query(func.max(Task.task_number)).filter(Task.space_id == space_id).scalar()
        return int(current_max or 0) + 1

    @classmethod
    def create_task(
        cls,
        *,
        space: TaskSpace,
        heading: str | None,
        description: str | None,
        raw_tags: str | None,
        reporter_name: str | None,
        assignee_name: str | None,
        status: str | None,
        priority: str | None,
        original_estimate_days: str | None,
        due_date: str | None,
    ) -> Task:
        normalized_heading = cls.require_text(heading, "Task heading", cls.MAX_HEADING_LENGTH)
        normalized_description = cls.require_text(
            description,
            "Task description",
            cls.MAX_DESCRIPTION_LENGTH,
        )
        normalized_reporter = cls.require_text(reporter_name, "Reporter", cls.MAX_NAME_LENGTH)
        normalized_assignee = cls.optional_text(assignee_name, cls.MAX_NAME_LENGTH)
        normalized_status = cls.validate_status(status or "todo", allow_closed=False)
        normalized_priority = cls.validate_priority(priority)
        tags = cls.parse_optional_tags(raw_tags)

        task = Task(
            space_id=space.id,
            task_number=cls.next_task_number(space.id),
            heading=normalized_heading,
            description=normalized_description,
            tags_csv=cls.tags_to_csv(tags),
            reporter_name=normalized_reporter,
            assignee_name=normalized_assignee,
            status=normalized_status,
            priority=normalized_priority,
            original_estimate_days=cls.parse_optional_estimate(original_estimate_days),
            due_date=cls.parse_optional_due_date(due_date),
        )
        task.space = space
        db.session.add(task)
        db.session.flush()
        cls.apply_status_transition(task, normalized_status, normalized_reporter, initial=True)
        return task

    @staticmethod
    def normalize_entity_ids(raw_values) -> list[str]:
        if raw_values is None:
            return []
        if isinstance(raw_values, str):
            values = [raw_values]
        else:
            values = raw_values

        normalized = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    @staticmethod
    def _rows_by_id(model, ids: list[str], label: str) -> list:
        if not ids:
            return []
        rows = model.query.filter(model.id.in_(ids)).all()
        by_id = {str(row.id): row for row in rows}
        missing = [row_id for row_id in ids if row_id not in by_id]
        if missing:
            raise ValueError(f"One or more selected {label} were not found")
        return [by_id[row_id] for row_id in ids]

    @staticmethod
    def _require_same_farm(rows: list, farm_id: str, label: str) -> None:
        if any(str(row.farm_id) != str(farm_id) for row in rows):
            raise ValueError(f"Selected {label} must belong to the task farm")

    @classmethod
    def add_entity_links(
        cls,
        *,
        task: Task,
        paddock_ids=None,
        water_asset_ids=None,
        mob_ids=None,
    ) -> list[TaskEntityLink]:
        space = task.space or db.session.get(TaskSpace, task.space_id)
        farm_id = space.farm_id
        normalized_paddock_ids = cls.normalize_entity_ids(paddock_ids)
        normalized_water_asset_ids = cls.normalize_entity_ids(water_asset_ids)
        normalized_mob_ids = cls.normalize_entity_ids(mob_ids)

        paddocks = cls._rows_by_id(Paddock, normalized_paddock_ids, "paddocks")
        water_assets = cls._rows_by_id(WaterAsset, normalized_water_asset_ids, "water assets")
        mobs = cls._rows_by_id(Mob, normalized_mob_ids, "mobs")
        cls._require_same_farm(paddocks, farm_id, "paddocks")
        cls._require_same_farm(water_assets, farm_id, "water assets")
        cls._require_same_farm(mobs, farm_id, "mobs")

        existing = {
            ("paddock", str(link.paddock_id))
            for link in TaskEntityLink.query.filter_by(task_id=task.id).filter(TaskEntityLink.paddock_id.isnot(None))
        }
        existing.update(
            {
                ("water_asset", str(link.water_asset_id))
                for link in TaskEntityLink.query.filter_by(task_id=task.id).filter(
                    TaskEntityLink.water_asset_id.isnot(None)
                )
            }
        )
        existing.update(
            {
                ("mob", str(link.mob_id))
                for link in TaskEntityLink.query.filter_by(task_id=task.id).filter(TaskEntityLink.mob_id.isnot(None))
            }
        )

        links = []
        for paddock in paddocks:
            key = ("paddock", str(paddock.id))
            if key in existing:
                continue
            existing.add(key)
            link = TaskEntityLink(task_id=task.id, paddock_id=paddock.id)
            db.session.add(link)
            links.append(link)
        for asset in water_assets:
            key = ("water_asset", str(asset.id))
            if key in existing:
                continue
            existing.add(key)
            link = TaskEntityLink(task_id=task.id, water_asset_id=asset.id)
            db.session.add(link)
            links.append(link)
        for mob in mobs:
            key = ("mob", str(mob.id))
            if key in existing:
                continue
            existing.add(key)
            link = TaskEntityLink(task_id=task.id, mob_id=mob.id)
            db.session.add(link)
            links.append(link)
        return links

    @classmethod
    def update_task_metadata(
        cls,
        *,
        task: Task,
        heading: str | None,
        description: str | None,
        raw_tags: str | None,
        reporter_name: str | None,
        assignee_name: str | None,
        priority: str | None,
        original_estimate_days: str | None,
        due_date: str | None,
    ) -> Task:
        task.heading = cls.require_text(heading, "Task heading", cls.MAX_HEADING_LENGTH)
        task.description = cls.require_text(description, "Task description", cls.MAX_DESCRIPTION_LENGTH)
        task.tags_csv = cls.tags_to_csv(cls.parse_optional_tags(raw_tags))
        task.reporter_name = cls.require_text(reporter_name, "Reporter", cls.MAX_NAME_LENGTH)
        task.assignee_name = cls.optional_text(assignee_name, cls.MAX_NAME_LENGTH)
        task.priority = cls.validate_priority(priority)
        task.original_estimate_days = cls.parse_optional_estimate(original_estimate_days)
        task.due_date = cls.parse_optional_due_date(due_date)
        return task

    @classmethod
    def apply_status_transition(
        cls,
        task: Task,
        next_status: str | None,
        changed_by_name: str | None,
        note: str | None = None,
        *,
        when: datetime | None = None,
        initial: bool = False,
    ) -> TaskStatusTransition:
        normalized_status = cls.validate_status(next_status)
        normalized_changed_by = cls.require_text(changed_by_name, "Changed by", cls.MAX_NAME_LENGTH)
        normalized_note = cls.optional_text(note)

        if not initial and normalized_status == "closed" and not normalized_note:
            raise ValueError("Closing a task requires a note")

        if not initial and task.status == normalized_status:
            raise ValueError("Task is already in that status")

        changed_at = cls._ensure_aware(when or datetime.now(timezone.utc))
        previous_status = None if initial else task.status

        if task.started_at is None and normalized_status in TASK_START_STATUSES:
            task.started_at = changed_at

        if normalized_status == "closed":
            task.closed_at = changed_at
        elif previous_status == "closed" and normalized_status != "closed":
            task.closed_at = None

        task.status = normalized_status
        transition = TaskStatusTransition(
            task_id=task.id,
            from_status=previous_status,
            to_status=normalized_status,
            changed_by_name=normalized_changed_by,
            note=normalized_note,
            changed_at=changed_at,
        )
        db.session.add(transition)
        return transition

    @staticmethod
    def resolve_task_display_key(raw_value: str | None) -> Task | None:
        normalized = (raw_value or "").strip().upper()
        if not normalized or "-" not in normalized:
            return None
        space_key, task_number_text = normalized.rsplit("-", 1)
        if not task_number_text.isdigit():
            return None
        return (
            Task.query.join(TaskSpace)
            .filter(TaskSpace.key == space_key, Task.task_number == int(task_number_text))
            .first()
        )

    @classmethod
    def create_link(
        cls,
        *,
        source_task: Task | None = None,
        source_space: TaskSpace | None = None,
        target_task: Task | None = None,
        target_space: TaskSpace | None = None,
        link_type: str | None,
        note: str | None = None,
    ) -> TaskLink:
        if (source_task is None) == (source_space is None):
            raise ValueError("A link source must be either a task or a space")
        if (target_task is None) == (target_space is None):
            raise ValueError("A link target must be either a task or a space")
        if source_task is not None and target_task is not None and source_task.id == target_task.id:
            raise ValueError("Tasks cannot link to themselves")
        if source_space is not None and target_space is not None and source_space.id == target_space.id:
            raise ValueError("Spaces cannot link to themselves")

        normalized_type = cls.validate_link_type(link_type)
        normalized_note = cls.optional_text(note)
        source_task_id = source_task.id if source_task else None
        source_space_id = source_space.id if source_space else None
        target_task_id = target_task.id if target_task else None
        target_space_id = target_space.id if target_space else None

        existing = TaskLink.query.filter_by(
            source_task_id=source_task_id,
            source_space_id=source_space_id,
            target_task_id=target_task_id,
            target_space_id=target_space_id,
            link_type=normalized_type,
        ).first()
        if existing:
            raise ValueError("That link already exists")

        link = TaskLink(
            source_task_id=source_task_id,
            source_space_id=source_space_id,
            target_task_id=target_task_id,
            target_space_id=target_space_id,
            link_type=normalized_type,
            note=normalized_note,
        )
        db.session.add(link)
        return link

    @classmethod
    def is_task_overdue(
        cls,
        task: Task,
        *,
        tz_name: str | None = None,
        today: date | None = None,
    ) -> bool:
        if task.status == "closed" or task.due_date is None:
            return False
        comparison_date = today or cls.today_for_timezone(tz_name or "UTC")
        return task.due_date < comparison_date
