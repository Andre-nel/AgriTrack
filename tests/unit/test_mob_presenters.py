from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingSession,
    Mob,
    MobEvent,
    Paddock,
)
from app.modules.mobs.presenters import build_mob_detail_context


def test_build_mob_detail_context_filters_events_and_builds_options(app):
    with app.app_context():
        farm = Farm(name="Mob Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=8)
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        other_mob = Mob(farm_id=farm.id, name="Other Mob", status="active")
        group_type = AnimalGroupType(
            species="Sheep",
            breed="Merino",
            sex="ewe",
            age_class="adult",
        )
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        db.session.add_all([paddock, mob, other_mob, group_type])
        db.session.flush()
        session.mob_id = mob.id
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction="1.0",
            )
        )
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group_type.id, head_count=12))
        db.session.add(
            MobEvent(
                mob_id=mob.id,
                farm_id=farm.id,
                tags_csv="health,condition",
                description="Checked condition",
            )
        )
        db.session.add(
            MobEvent(
                mob_id=mob.id,
                farm_id=farm.id,
                tags_csv="movement",
                description="Moved paddock",
            )
        )
        db.session.commit()

        context = build_mob_detail_context(mob, selected_event_tag="health")

        assert context["move_farms"] == [{"id": str(farm.id), "name": "Mob Farm"}]
        assert context["move_paddocks_by_farm"][str(farm.id)] == [
            {"id": str(paddock.id), "name": "North Camp"}
        ]
        assert context["transfer_mobs_by_farm"][str(farm.id)] == [
            {"id": str(other_mob.id), "name": "Other Mob"}
        ]
        assert context["current_allocations"] == [
            {
                "paddock_id": str(paddock.id),
                "paddock_name": "North Camp",
                "allocation_pct": 100.0,
            }
        ]
        assert context["split_group_options"][0]["head_count"] == 12
        assert [row["description"] for row in context["mob_events"]] == ["Checked condition"]
        assert context["event_tag_options"] == ["condition", "health", "movement"]
