from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import Incident
from app.services.task_service import TaskService


class IncidentService:
    MAX_CATEGORY_LENGTH = 120
    MAX_NOTE_LENGTH = TaskService.MAX_DESCRIPTION_LENGTH
    MAX_REPORTER_LENGTH = TaskService.MAX_NAME_LENGTH

    @staticmethod
    def parse_required_date(raw_value: str | date | None, field_name: str) -> date:
        if isinstance(raw_value, date):
            return raw_value
        text = (raw_value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be valid (YYYY-MM-DD)") from exc

    @classmethod
    def parse_optional_date(cls, raw_value: str | date | None, field_name: str) -> date | None:
        if raw_value is None or raw_value == "":
            return None
        return cls.parse_required_date(raw_value, field_name)

    @staticmethod
    def _tags_csv(raw_tags: str | list[str] | None) -> str:
        if isinstance(raw_tags, list):
            raw_tags = ",".join(str(value) for value in raw_tags)
        return TaskService.tags_to_csv(TaskService.parse_optional_tags(raw_tags))

    @staticmethod
    def tags_from_csv(raw_csv: str | None) -> list[str]:
        return TaskService.tags_from_csv(raw_csv)

    @classmethod
    def create_incident(
        cls,
        *,
        farm_id: str,
        occurred_on: str | date | None,
        category: str | None,
        note: str | None,
        raw_tags: str | list[str] | None,
        reported_by: str | None,
    ) -> Incident:
        normalized_farm_id = (farm_id or "").strip()
        if not normalized_farm_id:
            raise ValueError("Farm is required")
        incident = Incident(
            farm_id=normalized_farm_id,
            occurred_on=cls.parse_required_date(occurred_on, "Occurred on"),
            category=TaskService.require_text(category, "Incident category", cls.MAX_CATEGORY_LENGTH),
            note=TaskService.require_text(note, "Incident note", cls.MAX_NOTE_LENGTH),
            tags_csv=cls._tags_csv(raw_tags),
            reported_by=TaskService.require_text(reported_by, "Reported by", cls.MAX_REPORTER_LENGTH),
        )
        db.session.add(incident)
        return incident

    @classmethod
    def update_incident(
        cls,
        *,
        incident: Incident,
        farm_id: str,
        occurred_on: str | date | None,
        category: str | None,
        note: str | None,
        raw_tags: str | list[str] | None,
        reported_by: str | None,
    ) -> Incident:
        normalized_farm_id = (farm_id or "").strip()
        if not normalized_farm_id:
            raise ValueError("Farm is required")
        incident.farm_id = normalized_farm_id
        incident.occurred_on = cls.parse_required_date(occurred_on, "Occurred on")
        incident.category = TaskService.require_text(category, "Incident category", cls.MAX_CATEGORY_LENGTH)
        incident.note = TaskService.require_text(note, "Incident note", cls.MAX_NOTE_LENGTH)
        incident.tags_csv = cls._tags_csv(raw_tags)
        incident.reported_by = TaskService.require_text(reported_by, "Reported by", cls.MAX_REPORTER_LENGTH)
        return incident

    @classmethod
    def search_incidents(
        cls,
        *,
        farm_id: str | None = None,
        query: str | None = None,
        tag: str | None = None,
        category: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> list[Incident]:
        incident_query = Incident.query.options(selectinload(Incident.farm))
        normalized_farm_id = (farm_id or "").strip()
        if normalized_farm_id:
            incident_query = incident_query.filter(Incident.farm_id == normalized_farm_id)

        normalized_query = (query or "").strip()
        if normalized_query:
            like = f"%{normalized_query}%"
            incident_query = incident_query.filter(
                or_(
                    Incident.category.ilike(like),
                    Incident.note.ilike(like),
                    Incident.tags_csv.ilike(like),
                    Incident.reported_by.ilike(like),
                )
            )

        normalized_tag = " ".join((tag or "").strip().lower().split())
        if normalized_tag:
            incident_query = incident_query.filter(Incident.tags_csv.ilike(f"%{normalized_tag}%"))

        normalized_category = (category or "").strip()
        if normalized_category:
            incident_query = incident_query.filter(Incident.category.ilike(f"%{normalized_category}%"))

        parsed_start = cls.parse_optional_date(start_date, "Start date")
        parsed_end = cls.parse_optional_date(end_date, "End date")
        if parsed_start is not None:
            incident_query = incident_query.filter(Incident.occurred_on >= parsed_start)
        if parsed_end is not None:
            incident_query = incident_query.filter(Incident.occurred_on <= parsed_end)

        return incident_query.order_by(
            Incident.occurred_on.desc(),
            Incident.created_at.desc(),
            Incident.id.desc(),
        ).all()

    @classmethod
    def serialize_incident(cls, incident: Incident) -> dict:
        return {
            "id": str(incident.id),
            "farm_id": str(incident.farm_id),
            "farm_name": incident.farm.name if incident.farm else None,
            "occurred_on": incident.occurred_on.isoformat(),
            "category": incident.category,
            "note": incident.note,
            "tags": cls.tags_from_csv(incident.tags_csv),
            "reported_by": incident.reported_by,
        }

