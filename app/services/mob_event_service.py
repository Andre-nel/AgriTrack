import re
from datetime import datetime, timezone

from app.extensions import db
from app.models import MobEvent


class MobEventService:
    TAG_SPLIT_PATTERN = re.compile(r"[,;\n]+")
    MAX_TAGS = 20
    MAX_TAG_LENGTH = 40
    MAX_DESCRIPTION_LENGTH = 5000

    @staticmethod
    def _normalize_tag(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

    @classmethod
    def parse_tags(cls, raw_tags: str | None) -> list[str]:
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

        if not tags:
            raise ValueError("At least one tag is required")
        if len(tags) > cls.MAX_TAGS:
            raise ValueError(f"No more than {cls.MAX_TAGS} tags are allowed")
        return tags

    @staticmethod
    def tags_to_csv(tags: list[str]) -> str:
        return ",".join(tags)

    @classmethod
    def tags_from_csv(cls, raw_csv: str | None) -> list[str]:
        values = [cls._normalize_tag(value) for value in (raw_csv or "").split(",")]
        return [value for value in values if value]

    @classmethod
    def create_event(
        cls,
        mob_id: str,
        farm_id: str,
        description: str | None,
        raw_tags: str | None,
        event_at: datetime | None = None,
    ) -> MobEvent:
        normalized_description = (description or "").strip()
        if not normalized_description:
            raise ValueError("Description is required")
        if len(normalized_description) > cls.MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be {cls.MAX_DESCRIPTION_LENGTH} characters or fewer"
            )

        tags = cls.parse_tags(raw_tags)
        event = MobEvent(
            mob_id=mob_id,
            farm_id=farm_id,
            event_at=event_at or datetime.now(timezone.utc),
            tags_csv=cls.tags_to_csv(tags),
            description=normalized_description,
        )
        db.session.add(event)
        return event
