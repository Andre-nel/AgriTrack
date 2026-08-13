from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingSession,
    Mob,
    Paddock,
)
from app.services.movement_service import MovementService


def test_map_species_move_keeps_other_species_in_source_paddock(client, app):
    with app.app_context():
        farm = Farm(name="Map Species Drag Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        source = Paddock(farm_id=farm.id, name="North Species", area_ha=10, grazeable_area_ha=10)
        target = Paddock(farm_id=farm.id, name="South Species", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Mixed Species Mob", status="active")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([source, target, mob, cattle, sheep])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5),
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=sheep.id, head_count=12),
            ]
        )
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": str(source.id), "allocation_fraction": "1.0"}],
            destination_farm_id=str(farm.id),
        )
        db.session.commit()

        source_id = str(source.id)
        target_id = str(target.id)
        mob_id = str(mob.id)
        cattle_id = str(cattle.id)
        sheep_id = str(sheep.id)

    response = client.post(
        "/api/mobs/map-species-move",
        json={
            "source_paddock_id": source_id,
            "target_paddock_id": target_id,
            "species": "Cattle",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["moved_head_count"] == 5

    with app.app_context():
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {str(allocation.paddock_id): allocation for allocation in session.allocations}
        assert set(allocations) == {source_id, target_id}
        source_counts = {
            str(row.animal_group_type_id): row.head_count
            for row in allocations[source_id].group_assignments
        }
        target_counts = {
            str(row.animal_group_type_id): row.head_count
            for row in allocations[target_id].group_assignments
        }
        assert source_counts == {sheep_id: 12}
        assert target_counts == {cattle_id: 5}


def test_map_species_move_rejects_fractional_source_counts(client, app):
    with app.app_context():
        farm = Farm(name="Fractional Species Drag Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        source = Paddock(farm_id=farm.id, name="Fraction Source", area_ha=10, grazeable_area_ha=10)
        middle = Paddock(farm_id=farm.id, name="Fraction Middle", area_ha=10, grazeable_area_ha=10)
        target = Paddock(farm_id=farm.id, name="Fraction Target", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Fractional Species Mob", status="active")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add_all([source, middle, target, mob, cattle])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5)
        )
        MovementService.move_mob(
            mob=mob,
            allocations=[
                {"paddock_id": str(source.id), "allocation_fraction": "0.5"},
                {"paddock_id": str(middle.id), "allocation_fraction": "0.5"},
            ],
            destination_farm_id=str(farm.id),
        )
        db.session.commit()

        source_id = str(source.id)
        target_id = str(target.id)

    response = client.post(
        "/api/mobs/map-species-move",
        json={
            "source_paddock_id": source_id,
            "target_paddock_id": target_id,
            "species": "Cattle",
        },
    )

    assert response.status_code == 400
    assert "fractional head counts" in response.get_json()["error"]
