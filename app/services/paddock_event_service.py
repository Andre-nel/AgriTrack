from datetime import datetime, timezone

from app.extensions import db
from app.models import PaddockEvent
from app.services.mob_event_service import MobEventService


class PaddockEventService:
    DEFAULT_TAG = "field note"
    MAX_DESCRIPTION_LENGTH = MobEventService.MAX_DESCRIPTION_LENGTH

    @classmethod
    def create_event(
        cls,
        paddock_id: str,
        farm_id: str,
        description: str | None,
        raw_tags: str | None = None,
        event_at: datetime | None = None,
    ) -> PaddockEvent:
        normalized_description = (description or "").strip()
        if not normalized_description:
            raise ValueError("Description is required")
        if len(normalized_description) > cls.MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be {cls.MAX_DESCRIPTION_LENGTH} characters or fewer"
            )

        tags = MobEventService.parse_tags(raw_tags or cls.DEFAULT_TAG)
        event = PaddockEvent(
            paddock_id=paddock_id,
            farm_id=farm_id,
            event_at=event_at or datetime.now(timezone.utc),
            tags_csv=MobEventService.tags_to_csv(tags),
            description=normalized_description,
        )
        db.session.add(event)
        return event

    @staticmethod
    def tags_from_csv(raw_csv: str | None) -> list[str]:
        return MobEventService.tags_from_csv(raw_csv)
