from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, GrazingSession, Mob, Paddock
from app.models.stock_ledger import StockEventType
from app.modules.mobs.services import update_mob_balance_line_from_form
from app.services.gate_service import GateService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


def _farm_paddocks_and_mob(*, farm_name="Allocation Farm"):
    farm = Farm(name=farm_name, timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North", area_ha=10, grazeable_area_ha=10)
    south = Paddock(farm_id=farm.id, name="South", area_ha=30, grazeable_area_ha=30)
    mob = Mob(farm_id=farm.id, name="Allocation Mob", status="active")
    db.session.add_all([north, south, mob])
    db.session.flush()
    return farm, north, south, mob


def _group(species="Sheep", breed="Merino", sex="ewe", age_class="adult"):
    group = AnimalGroupType(species=species, breed=breed, sex=sex, age_class=age_class)
    db.session.add(group)
    db.session.flush()
    return group


def _add_balance(mob, group, head_count):
    db.session.add(
        AnimalGroupBalance(
            mob_id=mob.id,
            animal_group_type_id=group.id,
            head_count=head_count,
        )
    )


def _active_counts(mob_id: str) -> dict[str, dict[str, int]]:
    session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
    return {
        str(allocation.paddock_id): {
            str(assignment.animal_group_type_id): int(assignment.head_count)
            for assignment in allocation.group_assignments
        }
        for allocation in session.allocations
    }


def _move_with_counts(mob, north, south, ewe, ram):
    MovementService.move_mob(
        mob=mob,
        allocations=[
            {
                "paddock_id": str(north.id),
                "group_counts": [
                    {"animal_group_type_id": str(ewe.id), "head_count": 10},
                    {"animal_group_type_id": str(ram.id), "head_count": 8},
                ],
            },
            {
                "paddock_id": str(south.id),
                "group_counts": [
                    {"animal_group_type_id": str(ewe.id), "head_count": 10},
                    {"animal_group_type_id": str(ram.id), "head_count": 2},
                ],
            },
        ],
        destination_farm_id=str(mob.farm_id),
        allocation_mode="counts",
        when=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def test_stock_out_reduces_only_that_animal_type_by_existing_distribution(app):
    farm, north, south, mob = _farm_paddocks_and_mob()
    ewe = _group(sex="ewe")
    ram = _group(sex="ram")
    _add_balance(mob, ewe, 20)
    _add_balance(mob, ram, 10)
    _move_with_counts(mob, north, south, ewe, ram)
    db.session.commit()

    StockService.adjust_stock(
        mob_id=mob.id,
        farm_id=farm.id,
        animal_group_type_id=ram.id,
        event_type=StockEventType.missing,
        quantity=5,
        event_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    assert counts[str(north.id)] == {str(ewe.id): 10, str(ram.id): 4}
    assert counts[str(south.id)] == {str(ewe.id): 10, str(ram.id): 1}


def test_selected_paddock_stock_adjustment_applies_delta_to_that_paddock_only(app):
    farm, north, south, mob = _farm_paddocks_and_mob(farm_name="Selected Paddock Farm")
    ewe = _group(sex="ewe")
    ram = _group(sex="ram")
    _add_balance(mob, ewe, 20)
    _add_balance(mob, ram, 10)
    _move_with_counts(mob, north, south, ewe, ram)
    db.session.commit()

    StockService.adjust_stock(
        mob_id=mob.id,
        farm_id=farm.id,
        animal_group_type_id=ram.id,
        event_type=StockEventType.missing,
        quantity=2,
        event_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
        allocation_paddock_id=str(south.id),
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    assert counts[str(north.id)] == {str(ewe.id): 10, str(ram.id): 8}
    assert counts[str(south.id)] == {str(ewe.id): 10}


def test_count_down_to_zero_removes_animal_type_assignments(app):
    farm, north, south, mob = _farm_paddocks_and_mob(farm_name="Zero Count Farm")
    ewe = _group(sex="ewe")
    ram = _group(sex="ram")
    _add_balance(mob, ewe, 20)
    _add_balance(mob, ram, 10)
    _move_with_counts(mob, north, south, ewe, ram)
    db.session.commit()

    StockService.adjust_stock(
        mob_id=mob.id,
        farm_id=farm.id,
        animal_group_type_id=ram.id,
        event_type=StockEventType.missing,
        quantity=10,
        event_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    assert counts[str(north.id)] == {str(ewe.id): 10}
    assert counts[str(south.id)] == {str(ewe.id): 10}


def test_reclassification_preserves_source_paddock_distribution(app):
    _farm, north, south, mob = _farm_paddocks_and_mob(farm_name="Reclass Allocation Farm")
    ram = _group(sex="ram")
    _add_balance(mob, ram, 10)
    MovementService.move_mob(
        mob=mob,
        allocations=[
            {
                "paddock_id": str(north.id),
                "group_counts": [{"animal_group_type_id": str(ram.id), "head_count": 7}],
            },
            {
                "paddock_id": str(south.id),
                "group_counts": [{"animal_group_type_id": str(ram.id), "head_count": 3}],
            },
        ],
        destination_farm_id=str(mob.farm_id),
        allocation_mode="counts",
    )
    db.session.commit()

    update_mob_balance_line_from_form(
        mob,
        {
            "source_animal_group_type_id": str(ram.id),
            "sex": "wether",
            "age_class": "adult",
            "head_count": "8",
        },
    )

    wether = AnimalGroupType.query.filter_by(
        species="Sheep",
        breed="Merino",
        sex="wether",
        age_class="adult",
    ).one()
    counts = _active_counts(str(mob.id))
    assert counts[str(north.id)] == {str(wether.id): 6}
    assert counts[str(south.id)] == {str(wether.id): 2}


def test_new_animal_type_distributes_by_current_mob_split(app):
    farm, north, south, mob = _farm_paddocks_and_mob(farm_name="New Type Farm")
    ewe = _group(sex="ewe")
    ram = _group(sex="ram")
    _add_balance(mob, ewe, 20)
    MovementService.move_mob(
        mob=mob,
        allocations=[
            {
                "paddock_id": str(north.id),
                "group_counts": [{"animal_group_type_id": str(ewe.id), "head_count": 10}],
            },
            {
                "paddock_id": str(south.id),
                "group_counts": [{"animal_group_type_id": str(ewe.id), "head_count": 10}],
            },
        ],
        destination_farm_id=str(farm.id),
        allocation_mode="counts",
    )
    db.session.commit()

    StockService.adjust_stock(
        mob_id=mob.id,
        farm_id=farm.id,
        animal_group_type_id=ram.id,
        event_type=StockEventType.purchase,
        quantity=5,
        event_time=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    ram_counts = sorted(row[str(ram.id)] for row in counts.values())
    assert ram_counts == [2, 3]
    assert sum(ram_counts) == 5


def test_percentage_move_creates_exact_group_assignments(app):
    farm, north, south, mob = _farm_paddocks_and_mob(farm_name="Percentage Exact Farm")
    cattle = _group(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
    sheep = _group(sex="ewe")
    _add_balance(mob, cattle, 5)
    _add_balance(mob, sheep, 12)
    db.session.commit()

    MovementService.move_mob(
        mob=mob,
        allocations=[
            {"paddock_id": str(north.id), "allocation_fraction": "0.5"},
            {"paddock_id": str(south.id), "allocation_fraction": "0.5"},
        ],
        destination_farm_id=str(farm.id),
        allocation_mode="percentage",
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    assert set(counts) == {str(north.id), str(south.id)}
    assert sum(row[str(cattle.id)] for row in counts.values()) == 5
    assert sum(row[str(sheep.id)] for row in counts.values()) == 12
    assert sorted(row[str(cattle.id)] for row in counts.values()) == [2, 3]
    assert sorted(row[str(sheep.id)] for row in counts.values()) == [6, 6]


def test_gate_open_redistributes_each_animal_type_count(app):
    farm, north, south, mob = _farm_paddocks_and_mob(farm_name="Gate Count Farm")
    cattle = _group(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
    sheep = _group(sex="ewe")
    _add_balance(mob, cattle, 5)
    _add_balance(mob, sheep, 12)
    MovementService.move_mob(
        mob=mob,
        allocations=[
            {
                "paddock_id": str(north.id),
                "group_counts": [
                    {"animal_group_type_id": str(cattle.id), "head_count": 5},
                    {"animal_group_type_id": str(sheep.id), "head_count": 12},
                ],
            }
        ],
        destination_farm_id=str(farm.id),
        allocation_mode="counts",
    )
    gate = GateService.create_manual_gate(
        farm_id=str(farm.id),
        paddock_a_id=str(north.id),
        paddock_b_id=str(south.id),
    )
    db.session.commit()

    GateService.set_gate_state(
        gate,
        "open",
        event_time=datetime(2026, 6, 3, tzinfo=timezone.utc),
    )
    db.session.commit()

    counts = _active_counts(str(mob.id))
    assert counts[str(north.id)] == {str(cattle.id): 1, str(sheep.id): 3}
    assert counts[str(south.id)] == {str(cattle.id): 4, str(sheep.id): 9}
