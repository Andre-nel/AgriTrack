import pytest

from app.extensions import db
from app.models import Farm, JournalEntry
from app.modules.analytics.services import create_journal_entry


def test_create_journal_entry_normalizes_tags_and_commits(app):
    with app.app_context():
        farm = Farm(name="Journal Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.commit()

        entry = create_journal_entry(
            farm_id=str(farm.id),
            tags_raw="Health; health; Stock",
            description="  Checked mob condition. ",
            event_at_raw="2026-03-05T09:30:00",
        )

        saved = JournalEntry.query.filter_by(id=entry.id).first()
        assert saved is not None
        assert saved.farm_id == str(farm.id)
        assert saved.tags_csv == "health,stock"
        assert saved.description == "Checked mob condition."
        assert saved.event_at.isoformat() == "2026-03-05T09:30:00"


def test_create_journal_entry_rejects_missing_farm(app):
    with app.app_context():
        with pytest.raises(ValueError, match="Journal entry farm is required"):
            create_journal_entry(
                farm_id="missing",
                tags_raw="health",
                description="Checked mob condition.",
                event_at_raw="2026-03-05T09:30:00",
            )
