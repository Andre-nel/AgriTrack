"""A transfer submission keeps row destinations, notes, and stock changes together."""

from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalCohortLineage,
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    MobEvent,
    MovementEvent,
    Paddock,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


@pytest.fixture
def transfer_data(app):
    farm = Farm(name="Transfer farm", timezone="SAST", active=True)
    remote_farm = Farm(name="Remote transfer farm", timezone="SAST", active=True)
    db.session.add_all([farm, remote_farm])
    db.session.flush()
    mobs = {
        name: Mob(
            farm_id=remote_farm.id if name == "remote" else farm.id,
            name=f"Transfer {name}",
            status="archived" if name == "archived" else "active",
        )
        for name in ("source", "local", "local_other", "remote", "archived")
    }
    groups = {
        sex: AnimalGroupType(species="Sheep", breed="Merino", sex=sex, age_class="adult")
        for sex in ("ewe", "ram")
    }
    db.session.add_all([*mobs.values(), *groups.values()])
    db.session.flush()
    for name, mob in mobs.items():
        if name == "archived":
            continue
        counts = {"ewe": 10, "ram": 3} if name == "source" else {"ewe": 2}
        paddock = Paddock(
            farm_id=mob.farm_id, name=f"Camp {name}", area_ha=10, grazeable_area_ha=10,
        )
        db.session.add(paddock)
        db.session.add_all([
            AnimalGroupBalance(
                mob_id=mob.id, animal_group_type_id=groups[sex].id, head_count=quantity,
            )
            for sex, quantity in counts.items()
        ])
        db.session.flush()
        MovementService.move_mob(
            mob,
            [{
                "paddock_id": paddock.id,
                "group_counts": [
                    {"animal_group_type_id": groups[sex].id, "head_count": quantity}
                    for sex, quantity in counts.items()
                ],
            }],
            allocation_mode="counts",
            when=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    db.session.commit()
    return {
        "farm": str(farm.id), "remote_farm": str(remote_farm.id),
        **{name: str(mob.id) for name, mob in mobs.items()},
        **{sex: str(group.id) for sex, group in groups.items()},
    }


def _row(data, destination="local", *, group="ewe", quantity="3", note="First row note"):
    return {
        "transfer_destination_farm_id": data["remote_farm" if destination == "remote" else "farm"],
        "transfer_destination_mob_id": data[destination],
        "transfer_group_id": data[group],
        "transfer_quantity": quantity,
        "transfer_note": note,
    }


def _post_rows(client, data, rows):
    payload = {}
    for row in rows:
        for field, value in row.items():
            payload.setdefault(field, []).append(value)
    return client.post(f"/mobs/{data['source']}/transfer", data=payload, follow_redirects=True)


def _stock_state():
    """Capture affected tables, including original grazing records, for rollback checks."""
    models = (
        AnimalGroupBalance, AnimalCohort, AnimalCohortLineage, StockLedgerEntry,
        Mob, MobEvent, MovementEvent, GrazingSession, GrazingAllocation,
        GrazingAllocationGroupAssignment, GrazingAllocationLsuHistory,
        GrazingAllocationLsuBreakdownHistory,
    )
    return {
        model.__tablename__: {
            row.id: tuple(row) for row in db.session.execute(db.select(model.__table__))
        }
        for model in models
    }


@pytest.mark.parametrize("second_destination", ["local", "local_other", "remote"])
def test_transfer_rows_keep_destinations_groups_notes_and_grazing_counts(
    client, app, transfer_data, second_destination,
):
    data = transfer_data
    rows = [
        _row(data),
        _row(data, second_destination, quantity="4", note="Second row note"),
        _row(data, group="ram", quantity="2", note="Ram row note"),
    ]
    response = _post_rows(client, data, rows)

    assert response.status_code == 200
    assert b'class="flash success"' in response.data
    assert StockService.group_head_count(data["source"], data["ewe"]) == 3
    assert StockService.group_head_count(data["source"], data["ram"]) == 1
    expected = {
        (data["source"], data["ewe"]): 3,
        (data["source"], data["ram"]): 1,
        (data["local"], data["ewe"]): 5,
        (data["local"], data["ram"]): 2,
        (data["local_other"], data["ewe"]): 2,
        (data["remote"], data["ewe"]): 2,
    }
    expected[(data[second_destination], data["ewe"])] += 4
    for (mob_id, group_id), quantity in expected.items():
        assert StockService.group_head_count(mob_id, group_id) == quantity
        live_history = GrazingAllocationLsuBreakdownHistory.query.filter_by(
            mob_id=mob_id, animal_group_type_id=group_id, effective_to=None,
        ).one()
        assert live_history.head_count == quantity
        assert int(live_history.allocated_head_count) == quantity

    assert StockLedgerEntry.query.count() == 6
    transfer_events = MobEvent.query.filter(MobEvent.tags_csv.like("%transfer%")).all()
    assert len(transfer_events) == 6
    for row in rows:
        note = row["transfer_note"]
        entries = StockLedgerEntry.query.filter_by(note=note).all()
        assert {
            (entry.mob_id, entry.farm_id, entry.event_type, entry.animal_group_type_id, entry.quantity)
            for entry in entries
        } == {
            (
                data["source"], data["farm"], StockEventType.transfer_out,
                row["transfer_group_id"], int(row["transfer_quantity"]),
            ),
            (
                row["transfer_destination_mob_id"], row["transfer_destination_farm_id"],
                StockEventType.transfer_in, row["transfer_group_id"], int(row["transfer_quantity"]),
            ),
        }
        matching_events = [event for event in transfer_events if note in event.description]
        assert {event.mob_id for event in matching_events} == {
            data["source"], row["transfer_destination_mob_id"],
        }
    assert len({entry.event_time for entry in StockLedgerEntry.query.all()}) == 1


def test_transfer_rows_can_empty_source_and_accept_blank_individual_notes(client, app, transfer_data):
    data = transfer_data
    response = _post_rows(client, data, [
        _row(data, quantity="10", note=""),
        _row(data, "remote", group="ram", quantity="3", note="Ram destination only"),
    ])

    assert b'class="flash success"' in response.data
    assert AnimalGroupBalance.query.filter_by(mob_id=data["source"]).count() == 0
    assert GrazingSession.query.filter_by(mob_id=data["source"], end_at=None).count() == 0
    assert GrazingAllocationLsuBreakdownHistory.query.filter_by(
        mob_id=data["source"], effective_to=None,
    ).count() == 0
    assert StockService.group_head_count(data["local"], data["ewe"]) == 12
    assert StockService.group_head_count(data["remote"], data["ram"]) == 3
    ewe_entries = StockLedgerEntry.query.filter_by(animal_group_type_id=data["ewe"]).all()
    assert {entry.note for entry in ewe_entries} == {
        "transfer to Transfer local", "transfer from Transfer source",
    }


def test_transfer_rows_overdrawing_a_shared_group_roll_back_all_stock_and_history(
    client, app, transfer_data,
):
    data = transfer_data
    before = _stock_state()
    response = _post_rows(client, data, [
        _row(data, quantity="4"),
        _row(data, "remote", quantity="7", note="Would overdraw across rows"),
    ])

    assert b'class="flash error"' in response.data
    assert _stock_state() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transfer_destination_farm_id", "missing-farm"),
        ("transfer_destination_farm_id", "farm"),
        ("transfer_destination_mob_id", "missing-mob"),
        ("transfer_destination_mob_id", "source"),
        ("transfer_destination_mob_id", "archived"),
        ("transfer_group_id", "missing-group"),
        ("transfer_quantity", "0"),
        ("transfer_quantity", "-1"),
        ("transfer_quantity", "1.5"),
        ("transfer_quantity", ""),
    ],
)
def test_invalid_later_transfer_row_leaves_every_row_unapplied(
    client, app, transfer_data, field, value,
):
    data = transfer_data
    invalid = _row(data, "remote", note="Invalid second row")
    invalid[field] = data.get(value, value)
    if value in {"source", "archived"}:
        invalid["transfer_destination_farm_id"] = data["farm"]
    before = _stock_state()

    response = _post_rows(client, data, [_row(data), invalid])

    assert b'class="flash error"' in response.data
    assert _stock_state() == before


@pytest.mark.parametrize("missing_field", [
    "transfer_destination_farm_id", "transfer_destination_mob_id", "transfer_group_id",
    "transfer_quantity", "transfer_note",
])
def test_transfer_rows_reject_mismatched_fields_without_applying_the_first_row(
    client, app, transfer_data, missing_field,
):
    data = transfer_data
    incomplete = _row(data, "remote", note="Incomplete second row")
    incomplete.pop(missing_field)
    before = _stock_state()

    response = _post_rows(client, data, [_row(data), incomplete])

    assert b'class="flash error"' in response.data
    assert _stock_state() == before


def test_transfer_rows_preserve_cohorts_and_history_when_successive_rows_empty_paddocks(
    client, app, transfer_data,
):
    data = transfer_data
    source = db.session.get(Mob, data["source"])
    ewe_balance = AnimalGroupBalance.query.filter_by(
        mob_id=source.id, animal_group_type_id=data["ewe"],
    ).one()
    pregnant = CohortService.ensure_balance_cohort(ewe_balance)
    pregnant.reproductive_state = "pregnant"
    ewe_balance.head_count = 6
    not_pregnant = CohortService.create_cohort(
        animal_group_type_id=data["ewe"], origin_farm_id=data["farm"],
        reproductive_state="not_pregnant",
    )
    db.session.add(AnimalGroupBalance(
        mob_id=source.id, animal_group_type_id=data["ewe"],
        cohort_id=not_pregnant.id, head_count=4,
    ))
    source_session = GrazingSession.query.filter_by(mob_id=source.id, end_at=None).one()
    ewe_paddock_id = source_session.allocations[0].paddock_id
    ram_paddock = Paddock(farm_id=data["farm"], name="Separate ram camp", area_ha=10)
    db.session.add(ram_paddock)
    db.session.commit()
    original_session = MovementService.move_mob(
        source,
        [
            {"paddock_id": ewe_paddock_id, "group_counts": [
                {"animal_group_type_id": data["ewe"], "head_count": 10},
            ]},
            {"paddock_id": ram_paddock.id, "group_counts": [
                {"animal_group_type_id": data["ram"], "head_count": 3},
            ]},
        ],
        allocation_mode="counts", when=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.session.commit()
    original_allocation_ids = {row.id for row in original_session.allocations}
    history_before = {
        model: {
            row.id: (row.grazing_allocation_id, row.effective_from, row.allocated_lsu)
            for row in model.query.filter_by(grazing_session_id=original_session.id)
        }
        for model in (GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory)
    }

    response = _post_rows(client, data, [
        _row(data, quantity="3"),
        _row(data, "remote", quantity="7", note="Empty ewe camp"),
        _row(data, group="ram", quantity="3", note="Empty remaining ram camp"),
    ])

    assert b'class="flash success"' in response.data
    assert AnimalGroupBalance.query.filter_by(mob_id=source.id).count() == 0
    assert GrazingSession.query.filter_by(mob_id=source.id, end_at=None).count() == 0
    assert StockService.group_head_count(data["local"], data["ewe"]) == 5
    assert StockService.group_head_count(data["local"], data["ram"]) == 3
    assert StockService.group_head_count(data["remote"], data["ewe"]) == 9
    # The first row splits both source cohorts; later rows retain the parents' states.
    for destination, expected in (("local", {"pregnant": 2, "not_pregnant": 1}),
                                  ("remote", {"pregnant": 4, "not_pregnant": 3})):
        assert {
            balance.cohort.reproductive_state: balance.head_count
            for balance in AnimalGroupBalance.query.filter_by(
                mob_id=data[destination], animal_group_type_id=data["ewe"],
            )
            if balance.cohort is not None
        } == expected
    assert {row.parent_cohort_id for row in AnimalCohortLineage.query.all()} == {
        pregnant.id, not_pregnant.id,
    }
    [transfer_time] = {row.event_time for row in StockLedgerEntry.query.all()}
    assert original_session.end_at == transfer_time
    assert {row.id for row in original_session.allocations} == original_allocation_ids
    for model, records in history_before.items():
        assert records
        assert model.query.filter_by(mob_id=source.id, effective_to=None).count() == 0
        for row_id, values in records.items():
            row = db.session.get(model, row_id)
            assert row is not None
            assert (row.grazing_allocation_id, row.effective_from, row.allocated_lsu) == values
            assert row.effective_to == transfer_time
