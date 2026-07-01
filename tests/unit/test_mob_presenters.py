from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
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
        assert len(context["current_allocations"]) == 1
        allocation_row = context["current_allocations"][0]
        assert allocation_row["paddock_id"] == str(paddock.id)
        assert allocation_row["paddock_name"] == "North Camp"
        assert allocation_row["allocation_pct"] == 100.0
        assert allocation_row["has_exact_group_counts"] is False
        assert allocation_row["group_rows"][0]["label"] == "Sheep | Merino | ewe | adult"
        assert allocation_row["group_rows"][0]["head_count_display"] == "12.00"
        assert allocation_row["group_rows"][0]["source_label"] == "percentage-derived"
        assert context["initial_count_allocation_rows"] == [
            {"paddock_id": "", "group_counts": {str(group_type.id): 0}}
        ]
        assert context["split_group_options"][0]["head_count"] == 12
        assert [row["description"] for row in context["mob_events"]] == ["Checked condition"]
        assert context["event_tag_options"] == ["condition", "health", "movement"]


def test_build_mob_detail_context_prefills_exact_count_allocations(app):
    with app.app_context():
        farm = Farm(name="Exact Count Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Count", area_ha=10, grazeable_area_ha=8)
        south = Paddock(farm_id=farm.id, name="South Count", area_ha=10, grazeable_area_ha=8)
        mob = Mob(farm_id=farm.id, name="Exact Mob", status="active")
        ewes = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add_all([north, south, mob, ewes, cattle])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=ewes.id, head_count=12),
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5),
            ]
        )
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        north_allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=north.id,
            allocation_fraction="0.2857",
        )
        south_allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=south.id,
            allocation_fraction="0.7143",
        )
        db.session.add_all([north_allocation, south_allocation])
        db.session.flush()
        db.session.add_all(
            [
                GrazingAllocationGroupAssignment(
                    grazing_allocation_id=north_allocation.id,
                    animal_group_type_id=ewes.id,
                    head_count=12,
                    group_fraction="1.0",
                    assigned_lsu="2.0",
                ),
                GrazingAllocationGroupAssignment(
                    grazing_allocation_id=south_allocation.id,
                    animal_group_type_id=cattle.id,
                    head_count=5,
                    group_fraction="1.0",
                    assigned_lsu="5.0",
                ),
            ]
        )
        db.session.commit()

        context = build_mob_detail_context(mob, selected_event_tag="")

        assert [row["paddock_name"] for row in context["current_allocations"]] == [
            "North Count",
            "South Count",
        ]
        assert context["current_allocations"][0]["group_rows"][0]["head_count_display"] == "12"
        assert context["current_allocations"][0]["group_rows"][0]["source_label"] == "exact count"
        assert context["initial_count_allocation_rows"] == [
            {
                "paddock_id": str(north.id),
                "group_counts": {str(cattle.id): 0, str(ewes.id): 12},
            },
            {
                "paddock_id": str(south.id),
                "group_counts": {str(cattle.id): 5, str(ewes.id): 0},
            },
        ]
