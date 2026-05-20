from datetime import datetime, timezone

from app.extensions import db
from app.models import Farm, JournalEntry
from app.services.mob_event_service import MobEventService


def create_journal_entry(
    *,
    farm_id: str,
    tags_raw: str | None,
    description: str | None,
    event_at_raw: str | None,
) -> JournalEntry:
    farm = Farm.query.filter_by(id=(farm_id or "").strip()).first()
    if not farm:
        raise ValueError("Journal entry farm is required")

    try:
        tags = MobEventService.parse_tags(tags_raw)
        description_text = (description or "").strip()
        if not description_text:
            raise ValueError("Description is required")
        if len(description_text) > MobEventService.MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"Description must be {MobEventService.MAX_DESCRIPTION_LENGTH} characters or fewer"
            )

        event_at_text = (event_at_raw or "").strip()
        if event_at_text:
            event_at = datetime.fromisoformat(event_at_text)
            if event_at.tzinfo is None:
                event_at = event_at.replace(tzinfo=timezone.utc)
        else:
            event_at = datetime.now(timezone.utc)

        entry = JournalEntry(
            farm_id=farm.id,
            event_at=event_at,
            tags_csv=MobEventService.tags_to_csv(tags),
            description=description_text,
        )
        db.session.add(entry)
        db.session.commit()
        return entry
    except ValueError:
        db.session.rollback()
        raise
