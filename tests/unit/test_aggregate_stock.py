from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.models import (
    AnimalCohort, AnimalCohortLineage, AnimalGroupBalance, AnimalGroupType,
    Farm, GrazingSession, Mob, MovementEvent, Paddock, StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.allocation_distribution_service import AllocationDistributionService
from app.services.cohort_service import CohortService
from app.services.movement_service import MovementService
from app.services.stock_groups import apportion_counts
from app.services.stock_service import StockService


@pytest.fixture
def stock(app):
    with app.app_context():
        farm = Farm(name="Aggregate farm", timezone="SAST", active=True)
        mob = Mob(farm=farm, name="Source", status="active")
        destination = Mob(farm=farm, name="Destination", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([farm, mob, destination, group])
        db.session.flush()
        cohorts = []
        for balance_id, count, state in [("balance-a", 6, "pregnant"), ("balance-b", 4, "not_pregnant")]:
            cohort = CohortService.create_cohort(
                animal_group_type_id=group.id, origin_farm_id=farm.id, reproductive_state=state,
            )
            db.session.add(AnimalGroupBalance(
                id=balance_id, mob_id=mob.id, animal_group_type_id=group.id,
                cohort_id=cohort.id, head_count=count,
            ))
            cohorts.append(cohort)
        db.session.commit()
        yield farm, mob, destination, group, cohorts


def adjust(stock, event, quantity, **kwargs):
    farm, mob, _destination, group, _cohorts = stock
    return StockService.adjust_group_stock(
        mob_id=mob.id, farm_id=farm.id, animal_group_type_id=group.id,
        event_type=event, quantity=quantity, **kwargs,
    )


def counts_by_state(mob, group):
    return {
        row.cohort.reproductive_state: row.head_count
        for row in StockService.group_balances(mob.id, group.id)
    }


def test_apportion_conserves_integer_counts_with_stable_ties():
    assert apportion_counts(1, [("b", 1), ("a", 1)]) == {"b": 0, "a": 1}
    for first in range(1, 8):
        for second in range(1, 8):
            for requested in range(first + second + 1):
                shares = apportion_counts(requested, [("a", first), ("b", second)])
                assert sum(shares.values()) == requested
                assert 0 <= shares["a"] <= first
                assert 0 <= shares["b"] <= second


def test_group_summary_combines_legacy_and_cohort_balances(stock):
    _farm, mob, _destination, group, _cohorts = stock
    db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=2))
    other = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="adult")
    db.session.add(other)
    db.session.flush()
    db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=other.id, head_count=3))
    db.session.commit()
    summaries = StockService.group_summary_rows(mob)
    assert {row["animal_group_type_id"]: row["head_count"] for row in summaries} == {group.id: 12, other.id: 3}


def test_matching_aggregate_count_does_not_create_records(stock):
    assert adjust(stock, StockEventType.count, 10) == []
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohort.query.count() == 2
    assert AnimalCohortLineage.query.count() == 0


def test_count_reduction_is_proportional_and_preserves_states(stock):
    _farm, mob, _destination, group, _cohorts = stock
    entries = adjust(stock, StockEventType.count, 7)
    assert counts_by_state(mob, group) == {"pregnant": 4, "not_pregnant": 3}
    assert sorted(row.quantity for row in entries) == [1, 2]
    assert all(row.event_type == StockEventType.missing for row in entries)
    assert all("proportionally" in row.note for row in entries)


@pytest.mark.parametrize("event", [StockEventType.purchase, StockEventType.birth, StockEventType.adjustment_in])
def test_additions_receive_fresh_unrecorded_identity(stock, event):
    _farm, mob, _destination, group, parents = stock
    entries = adjust(stock, event, 3)
    assert len(entries) == 1
    assert entries[0].cohort_id not in {row.id for row in parents}
    assert counts_by_state(mob, group) == {"pregnant": 6, "not_pregnant": 4, "not_recorded": 3}
    assert AnimalCohortLineage.query.count() == 0


def test_positive_count_correction_does_not_enlarge_observed_cohorts(stock):
    _farm, mob, _destination, group, _parents = stock
    entries = adjust(stock, StockEventType.count, 13)
    assert entries[0].quantity == 3
    assert counts_by_state(mob, group) == {"pregnant": 6, "not_pregnant": 4, "not_recorded": 3}


def test_zero_count_removes_all_balances_but_keeps_cohort_history(stock):
    _farm, mob, _destination, group, _parents = stock
    entries = adjust(stock, StockEventType.count, 0)
    assert sum(row.quantity for row in entries) == 10
    assert StockService.group_head_count(mob.id, group.id) == 0
    assert AnimalCohort.query.count() == 2


@pytest.mark.parametrize("event,quantity", [
    (StockEventType.count, -1), (StockEventType.count, 1.5), (StockEventType.count, True),
    (StockEventType.death, 0), (StockEventType.death, 11),
])
def test_invalid_group_adjustment_has_no_partial_postings(stock, event, quantity):
    with pytest.raises(ValueError):
        adjust(stock, event, quantity)
    assert AnimalGroupBalance.query.count() == 2
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohort.query.count() == 2


def test_transfer_spans_cohorts_and_preserves_lineage(stock):
    farm, source, destination, group, parents = stock
    MovementService.transfer_stock_between_mobs(
        source, destination, [{"animal_group_type_id": group.id, "quantity": 3}],
        destination_farm_id=farm.id,
    )
    assert counts_by_state(source, group) == {"pregnant": 4, "not_pregnant": 3}
    assert counts_by_state(destination, group) == {"pregnant": 2, "not_pregnant": 1}
    assert {row.parent_cohort_id for row in AnimalCohortLineage.query.all()} == {row.id for row in parents}
    assert sum(row.quantity for row in StockLedgerEntry.query.filter_by(event_type=StockEventType.transfer_out)) == 3


def test_legacy_explicit_cohort_transfer_remains_exact(stock):
    _farm, source, destination, group, parents = stock
    MovementService.transfer_stock_between_mobs(source, destination, [{
        "animal_group_type_id": group.id, "cohort_id": parents[0].id, "quantity": 3,
    }])
    assert counts_by_state(source, group) == {"pregnant": 3, "not_pregnant": 4}
    assert counts_by_state(destination, group) == {"pregnant": 3}


def test_legacy_cohort_selector_in_group_id_stays_explicit(stock):
    _farm, source, destination, group, parents = stock
    MovementService.transfer_stock_between_mobs(source, destination, [{
        "animal_group_type_id": f" cohort:{parents[0].id} ", "quantity": 3,
    }])
    assert counts_by_state(source, group) == {"pregnant": 3, "not_pregnant": 4}
    assert counts_by_state(destination, group) == {"pregnant": 3}


def test_mixed_aggregate_and_explicit_transfers_reserve_exact_cohort_first(stock):
    _farm, source, destination, group, parents = stock
    MovementService.transfer_stock_between_mobs(source, destination, [
        {"animal_group_type_id": group.id, "quantity": 4},
        {"animal_group_type_id": group.id, "cohort_id": parents[0].id, "quantity": 4},
    ])
    # Reserve four pregnant females; proportionally take 1 + 3 from the remaining 2 + 4.
    assert counts_by_state(source, group) == {"pregnant": 1, "not_pregnant": 1}
    assert counts_by_state(destination, group) == {"pregnant": 5, "not_pregnant": 3}


def test_mismatched_cohort_and_group_are_rejected_without_posting(stock):
    _farm, source, destination, _group, parents = stock
    wrong_group = StockService.get_or_create_group_type("Sheep", "Merino", "ram", "adult")
    with pytest.raises(ValueError, match="does not match"):
        MovementService.transfer_stock_between_mobs(source, destination, [{
            "animal_group_type_id": wrong_group.id, "cohort_id": parents[0].id, "quantity": 1,
        }])
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohortLineage.query.count() == 0


def test_transfer_resolves_pending_group_taxonomy_without_creating_a_type(stock):
    _farm, source, destination, group, _parents = stock
    MovementService.transfer_stock_between_mobs(source, destination, [{
        "animal_group_type": {"species": "Sheep", "breed": "Merino", "sex": "ewe", "age_class": "adult"},
        "quantity": 3,
    }])
    assert StockService.group_head_count(destination.id, group.id) == 3
    assert AnimalGroupType.query.count() == 1


def test_exact_count_move_resolves_pending_group_taxonomy(stock):
    farm, source, _destination, group, _parents = stock
    paddock = Paddock(farm_id=farm.id, name="Pending type camp", area_ha=10, grazeable_area_ha=10)
    db.session.add(paddock)
    db.session.flush()
    session = MovementService.move_mob(source, [{
        "paddock_id": paddock.id,
        "group_counts": [{
            "animal_group_type": {"species": "Sheep", "breed": "Merino", "sex": "ewe", "age_class": "adult"},
            "head_count": 10,
        }],
    }], allocation_mode="counts")
    assignments = [row for allocation in session.allocations for row in allocation.group_assignments]
    assert [(row.animal_group_type_id, row.head_count) for row in assignments] == [(group.id, 10)]
    assert AnimalGroupType.query.count() == 1


@pytest.mark.parametrize("group_selector", [
    {"animal_group_type_id": "pending-group"},
    {"animal_group_type": {"species": "Sheep", "breed": "Unknown breed", "sex": "ewe", "age_class": "adult"}},
])
def test_exact_count_move_does_not_create_or_accept_unknown_types(stock, group_selector):
    farm, source, _destination, _group, _parents = stock
    paddock = Paddock(farm_id=farm.id, name="Invalid type camp", area_ha=10, grazeable_area_ha=10)
    db.session.add(paddock)
    db.session.flush()
    with pytest.raises(ValueError):
        MovementService.move_mob(source, [{
            "paddock_id": paddock.id, "group_counts": [{**group_selector, "head_count": 10}],
        }], allocation_mode="counts")
    assert AnimalGroupType.query.count() == 1
    assert GrazingSession.query.count() == 0


def test_multiple_split_destinations_conserve_source_quota(stock):
    _farm, source, _destination, group, _parents = stock
    created = MovementService.split_mob(source, [
        {"name": "First", "groups": [{"animal_group_type_id": group.id, "quantity": 2}]},
        {"name": "Second", "groups": [{"animal_group_type_id": group.id, "quantity": 3}]},
    ])
    assert [StockService.group_head_count(mob.id, group.id) for mob in created] == [2, 3]
    assert counts_by_state(source, group) == {"pregnant": 3, "not_pregnant": 2}
    assert sum(row.head_count for row in AnimalGroupBalance.query.all()) == 10
    assert all(row.movement_event_id for row in AnimalCohortLineage.query.all())


def test_full_multidestination_split_keeps_parents_with_final_remaining_portions(stock):
    _farm, source, _destination, group, parents = stock
    parent_ids = {row.id for row in parents}
    created = MovementService.split_mob(source, [
        {"name": "First", "groups": [{"animal_group_type_id": group.id, "quantity": 4}]},
        {"name": "Second", "groups": [{"animal_group_type_id": group.id, "quantity": 6}]},
    ])
    assert StockService.group_head_count(source.id, group.id) == 0
    assert {row.cohort_id for row in StockService.group_balances(created[1].id, group.id)} == parent_ids
    assert not ({row.cohort_id for row in StockService.group_balances(created[0].id, group.id)} & parent_ids)
    assert counts_by_state(created[0], group) == {"pregnant": 2, "not_pregnant": 2}
    assert counts_by_state(created[1], group) == {"pregnant": 4, "not_pregnant": 2}
    for parent in parents:
        descendants = CohortService.descendants(parent.id)
        assert sum(row.head_count for row in AnimalGroupBalance.query.filter(AnimalGroupBalance.cohort_id.in_(descendants))) == (6 if parent.reproductive_state == "pregnant" else 4)


def test_invalid_split_is_validated_before_creating_mobs_or_lineage(stock):
    _farm, source, _destination, group, _parents = stock
    with pytest.raises(ValueError, match="exceed source stock"):
        MovementService.split_mob(source, [
            {"name": "First", "groups": [{"animal_group_type_id": group.id, "quantity": 6}]},
            {"name": "Second", "groups": [{"animal_group_type_id": group.id, "quantity": 5}]},
        ])
    assert Mob.query.count() == 2
    assert MovementEvent.query.count() == 0
    assert AnimalCohortLineage.query.count() == 0


def test_reclassification_preserves_shared_parent_and_existing_target(stock):
    farm, source, other_mob, group, parents = stock
    target = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="old")
    db.session.add(target)
    db.session.flush()
    db.session.add_all([
        AnimalGroupBalance(mob_id=other_mob.id, animal_group_type_id=group.id, cohort_id=parents[0].id, head_count=2),
        AnimalGroupBalance(mob_id=source.id, animal_group_type_id=target.id, head_count=5),
    ])
    db.session.commit()
    source_ids = {row.id for row in parents}
    StockService.reclassify_group_stock(source, group.id, target, 8)
    assert StockService.group_head_count(source.id, group.id) == 0
    assert StockService.group_head_count(source.id, target.id) == 13
    assert StockService.group_head_count(other_mob.id, group.id) == 2
    assert all(row.animal_group_type_id == group.id for row in parents)
    children = AnimalCohortLineage.query.filter(AnimalCohortLineage.parent_cohort_id.in_(source_ids)).all()
    assert sorted(row.quantity for row in children) == [3, 5]
    assert all(row.reason == "correction" for row in children)
    assert all(row.child_cohort.animal_group_type_id == target.id for row in children)


def test_reclassification_increase_adds_unrecorded_excess_and_zero_removes(stock):
    _farm, source, _other, group, _parents = stock
    target = StockService.get_or_create_group_type("Sheep", "Merino", "ewe", "old")
    StockService.reclassify_group_stock(source, group.id, target, 12)
    assert counts_by_state(source, target) == {"pregnant": 6, "not_pregnant": 4, "not_recorded": 2}
    assert sum(row.quantity for row in AnimalCohortLineage.query.all()) == 10
    StockService.reclassify_group_stock(source, target.id, group, 0)
    assert StockService.group_head_count(source.id, target.id) == 0
    assert StockService.group_head_count(source.id, group.id) == 0
    assert AnimalCohortLineage.query.count() == 2


def test_reclassification_to_nonfemale_clears_only_child_current_state(stock):
    _farm, source, _other, group, parents = stock
    target = StockService.get_or_create_group_type("Sheep", "Merino", "ram", "adult")
    StockService.reclassify_group_stock(source, group.id, target, 10)
    assert all(row.cohort.reproductive_state == "not_recorded" for row in StockService.group_balances(source.id, target.id))
    assert parents[0].reproductive_state == "pregnant"


def test_transfer_sync_keeps_all_types_and_paddock_totals(stock):
    farm, source, destination, ewe, _parents = stock
    ram = StockService.get_or_create_group_type("Sheep", "Merino", "ram", "adult")
    StockService.adjust_group_stock(source.id, farm.id, ram.id, StockEventType.purchase, 6)
    paddocks = [Paddock(farm_id=farm.id, name=name, area_ha=10, grazeable_area_ha=10) for name in ["North", "South", "Dest"]]
    db.session.add_all(paddocks)
    db.session.flush()
    MovementService.move_mob(source, [
        {"paddock_id": paddock.id, "group_counts": [
            {"animal_group_type_id": ewe.id, "head_count": 5},
            {"animal_group_type_id": ram.id, "head_count": 3},
        ]} for paddock in paddocks[:2]
    ], allocation_mode="counts")
    StockService.adjust_group_stock(destination.id, farm.id, ewe.id, StockEventType.purchase, 2)
    MovementService.move_mob(destination, [{"paddock_id": paddocks[2].id, "allocation_fraction": "1"}])
    db.session.commit()
    MovementService.transfer_stock_between_mobs(source, destination, [
        {"animal_group_type_id": ewe.id, "quantity": 3},
        {"animal_group_type_id": ram.id, "quantity": 2},
    ], when=datetime.now(timezone.utc))
    for mob, expected in [(source, {ewe.id: 7, ram.id: 4}), (destination, {ewe.id: 5, ram.id: 2})]:
        session = GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()
        # Read stored assignments directly: snapshot normalization could hide a bad write.
        stored = {
            group_id: sum(int(assignment.head_count) for allocation in session.allocations
                          for assignment in allocation.group_assignments if assignment.animal_group_type_id == group_id)
            for group_id in expected
        }
        assert stored == expected
        allocations = AllocationDistributionService.snapshot_session_counts(session)
        actual = {group_id: sum(counts.get(group_id, 0) for counts in allocations.values()) for group_id in expected}
        assert actual == expected
        assert {row["animal_group_type_id"]: row["head_count"] for row in StockService.group_summary_rows(mob)} == expected


def test_caller_rollback_reverts_partial_posting_and_lineage(stock, monkeypatch):
    _farm, source, destination, group, _parents = stock
    real_adjust = StockService.adjust_stock
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Simulated posting failure")
        return real_adjust(**kwargs)

    monkeypatch.setattr(StockService, "adjust_stock", fail_second)
    with pytest.raises(ValueError, match="Simulated"):
        MovementService.transfer_stock_between_mobs(source, destination, [{"animal_group_type_id": group.id, "quantity": 3}])
    db.session.rollback()
    assert StockService.group_head_count(source.id, group.id) == 10
    assert StockService.group_head_count(destination.id, group.id) == 0
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohortLineage.query.count() == 0
    assert AnimalCohort.query.count() == 2


@pytest.mark.parametrize("overrides", [
    {"mob_id": "missing"}, {"farm_id": "wrong-farm"}, {"animal_group_type_id": "missing"},
])
def test_group_adjustment_rejects_invalid_identity_even_for_noop(stock, overrides):
    farm, source, _destination, group, _parents = stock
    arguments = dict(mob_id=source.id, farm_id=farm.id, animal_group_type_id=group.id,
                     event_type=StockEventType.count, quantity=10)
    arguments.update(overrides)
    with pytest.raises(ValueError):
        StockService.adjust_group_stock(**arguments)
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohort.query.count() == 2


@pytest.mark.parametrize("overrides", [
    {"quantity": 1.5}, {"quantity": True}, {"quantity": 0}, {"quantity": 7},
    {"event_type": StockEventType.count}, {"farm_id": "wrong-farm"},
    {"animal_group_type_id": "missing"}, {"mob_id": "missing"},
])
def test_low_level_adjustment_rejects_invalid_input_before_writing(stock, overrides):
    farm, source, _destination, group, parents = stock
    arguments = dict(mob_id=source.id, farm_id=farm.id, animal_group_type_id=group.id,
                     cohort_id=parents[0].id, event_type=StockEventType.death, quantity=1)
    arguments.update(overrides)
    with pytest.raises(ValueError):
        StockService.adjust_stock(**arguments)
    assert StockLedgerEntry.query.count() == 0
    assert AnimalCohort.query.count() == 2
    assert StockService.group_head_count(source.id, group.id) == 10


def test_legacy_balance_overdraw_does_not_create_a_cohort(stock):
    farm, _source, destination, group, _parents = stock
    db.session.add(AnimalGroupBalance(mob_id=destination.id, animal_group_type_id=group.id, head_count=2))
    db.session.commit()
    with pytest.raises(ValueError, match="negative"):
        StockService.adjust_stock(destination.id, farm.id, group.id, StockEventType.death, 3)
    assert AnimalCohort.query.count() == 2
    assert StockLedgerEntry.query.count() == 0
    assert StockService.group_balances(destination.id, group.id)[0].cohort_id is None


def test_selected_paddock_failure_leaves_cohort_counts_unchanged(stock):
    farm, source, _destination, group, _parents = stock
    paddocks = [Paddock(farm_id=farm.id, name=name, area_ha=10, grazeable_area_ha=10) for name in ["Small", "Large"]]
    db.session.add_all(paddocks)
    db.session.flush()
    MovementService.move_mob(source, [
        {"paddock_id": paddock.id, "group_counts": [{"animal_group_type_id": group.id, "head_count": count}]}
        for paddock, count in zip(paddocks, [1, 9])
    ], allocation_mode="counts")
    db.session.commit()
    with pytest.raises(ValueError, match="enough"):
        adjust(stock, StockEventType.death, 3, allocation_paddock_id=paddocks[0].id)
    db.session.rollback()
    assert counts_by_state(source, group) == {"pregnant": 6, "not_pregnant": 4}
    assert StockLedgerEntry.query.count() == 0
    session = GrazingSession.query.filter_by(mob_id=source.id, end_at=None).one()
    assert sorted(int(assignment.head_count) for allocation in session.allocations for assignment in allocation.group_assignments) == [1, 9]
