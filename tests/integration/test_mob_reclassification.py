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
    Paddock,
    StockLedgerEntry,
)
from app.models.mob_inventory import MobInventoryBaselineGroup, MobInventoryStateHistory
from app.models.stock_ledger import StockEventType
from app.services.cohort_service import CohortService
from app.services.mob_event_service import MobEventService
from app.services.movement_service import MovementService
from app.services.stock_service import StockService


@pytest.fixture
def stock(app):
    with app.app_context():
        farm = Farm(name="Reclassification Farm")
        db.session.add(farm)
        db.session.flush()
        mob = Mob(farm_id=farm.id, name="Reclassification Ewes", status="active")
        source = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        target = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="old")
        db.session.add_all([mob, source, target])
        db.session.flush()
        db.session.add(AnimalGroupBalance(
            mob_id=mob.id, animal_group_type_id=source.id, head_count=100,
        ))
        db.session.commit()
        return {"mob": mob.id, "farm": farm.id, "source": source.id, "target": target.id}


def _form(stock, **overrides):
    return {
        "source_animal_group_type_id": stock["source"], "quantity": "26",
        "sex": "ewe", "age_class": "old", "note": "Age classification corrected",
        **overrides,
    }


def _snapshot():
    """Include history and identities so an unchanged count cannot hide a partial write."""
    models = (
        AnimalGroupType, AnimalGroupBalance, AnimalCohort, AnimalCohortLineage,
        StockLedgerEntry, MobEvent, MobInventoryStateHistory, MobInventoryBaselineGroup,
        GrazingSession, GrazingAllocation, GrazingAllocationGroupAssignment,
        GrazingAllocationLsuHistory, GrazingAllocationLsuBreakdownHistory,
    )
    return {
        model.__tablename__: sorted(
            (tuple(row) for row in db.session.execute(db.select(model.__table__))), key=repr,
        )
        for model in models
    }


@pytest.mark.parametrize("existing_old", [0, 7])
def test_reclassify_26_ewes_conserves_heads_and_records_atomic_history(client, app, stock, existing_old):
    with app.app_context():
        if existing_old:
            db.session.add(AnimalGroupBalance(
                mob_id=stock["mob"], animal_group_type_id=stock["target"], head_count=existing_old,
            ))
            db.session.commit()
        original_target = AnimalGroupBalance.query.filter_by(
            mob_id=stock["mob"], animal_group_type_id=stock["target"],
        ).first()
        original_target_id = original_target.id if original_target else None

    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify", data=_form(stock), follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Reclassified 26 head. Total head count unchanged." in response.data
    with app.app_context():
        assert StockService.group_head_count(stock["mob"], stock["source"]) == 74
        assert StockService.group_head_count(stock["mob"], stock["target"]) == existing_old + 26
        if original_target_id:
            original_target = db.session.get(AnimalGroupBalance, original_target_id)
            assert original_target.head_count == existing_old
            assert original_target.cohort_id is None
        entries = StockLedgerEntry.query.all()
        assert {(row.event_type, row.animal_group_type_id, row.quantity) for row in entries} == {
            (StockEventType.adjustment_out, stock["source"], 26),
            (StockEventType.adjustment_in, stock["target"], 26),
        }
        event = MobEvent.query.one()
        assert {row.event_time for row in entries} == {event.event_at}
        assert "Source balance 100 -> 74" in event.description
        assert f"destination balance {existing_old} -> {existing_old + 26}" in event.description
        assert all("Age classification corrected" in row.note for row in entries)
        expected_baseline = {stock["source"]: (100, 0)}
        if existing_old:
            expected_baseline[stock["target"]] = (existing_old, 0)
        assert {row.animal_group_type_id: (row.head_count, row.ledger_net)
                for row in MobInventoryBaselineGroup.query.all()} == expected_baseline


def test_reclassify_all_removes_source_row_and_ignores_submitted_species_breed(client, app, stock):
    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify",
        data=_form(stock, quantity="100", species="Cattle", breed="Bonsmara"),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert f'data-group-id="{stock["source"]}"'.encode() not in response.data
    assert f'data-group-id="{stock["target"]}"'.encode() in response.data
    with app.app_context():
        assert StockService.group_head_count(stock["mob"], stock["source"]) == 0
        assert StockService.group_head_count(stock["mob"], stock["target"]) == 100
        assert AnimalGroupType.query.count() == 2


def test_long_note_keeps_proportional_audit_annotation_within_event_limit(client, app, stock):
    with app.app_context():
        AnimalGroupBalance.query.filter_by(mob_id=stock["mob"]).one().head_count = 60
        cohort = CohortService.create_cohort(
            animal_group_type_id=stock["source"], origin_farm_id=stock["farm"],
        )
        db.session.add(AnimalGroupBalance(
            mob_id=stock["mob"], animal_group_type_id=stock["source"],
            cohort_id=cohort.id, head_count=40,
        ))
        db.session.commit()
    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify",
        data=_form(stock, note="Recorded age correction. " * 300), follow_redirects=True,
    )
    assert b"Reclassified 26 head" in response.data
    with app.app_context():
        event = MobEvent.query.one()
        assert len(event.description) <= MobEventService.MAX_DESCRIPTION_LENGTH
        assert "Cohort quantities allocated proportionally." in event.description
        assert "Note: Recorded age correction." in event.description
        assert StockService.group_head_count(stock["mob"], stock["source"]) == 74
        assert StockService.group_head_count(stock["mob"], stock["target"]) == 26


@pytest.mark.parametrize("overrides, message", [
    ({"quantity": ""}, "must be a whole number"),
    ({"quantity": "twenty-six"}, "must be a whole number"),
    ({"quantity": "1.5"}, "must be a whole number"),
    ({"quantity": "0"}, "must be between 1 and 100"),
    ({"quantity": "-1"}, "must be between 1 and 100"),
    ({"quantity": "101"}, "must be between 1 and 100"),
    ({"source_animal_group_type_id": ""}, "Select a balance line"),
    ({"source_animal_group_type_id": "unknown"}, "no longer available"),
    ({"age_class": "adult"}, "Choose a different sex or age class"),
    ({"sex": "cow"}, "Sex for Sheep must be one of"),
    ({"sex": ""}, "Sex for Sheep must be one of"),
    ({"age_class": "calf"}, "Age class for Sheep must be one of"),
    ({"age_class": ""}, "Age class for Sheep must be one of"),
    ({"head_count": "26"}, "Balance editing has changed"),
    ({"source_cohort_id": "old-selection"}, "Balance editing has changed"),
])
def test_invalid_reclassification_changes_nothing(client, app, stock, overrides, message):
    with app.app_context():
        before = _snapshot()
    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify",
        data=_form(stock, **overrides), follow_redirects=True,
    )
    assert message.encode() in response.data
    with app.app_context():
        assert _snapshot() == before


def test_reclassification_rechecks_current_stock_and_foreign_source(client, app, stock):
    with app.app_context():
        balance = AnimalGroupBalance.query.filter_by(mob_id=stock["mob"]).one()
        balance.head_count = 20
        other = Mob(farm_id=stock["farm"], name="Other mob", status="active")
        db.session.add(other)
        db.session.flush()
        db.session.add(AnimalGroupBalance(
            mob_id=other.id, animal_group_type_id=stock["target"], head_count=50,
        ))
        db.session.commit()
        before = _snapshot()
    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify", data=_form(stock), follow_redirects=True,
    )
    assert b"must be between 1 and 20" in response.data
    response = client.post(
        f"/mobs/{stock['mob']}/balances/reclassify",
        data=_form(stock, source_animal_group_type_id=stock["target"], age_class="adult"),
        follow_redirects=True,
    )
    assert b"no longer available" in response.data
    with app.app_context():
        assert _snapshot() == before


def test_legacy_balance_edit_rejects_old_form_without_writing(client, app, stock):
    with app.app_context():
        before = _snapshot()
    response = client.post(
        f"/mobs/{stock['mob']}/balances/edit",
        data={"source_animal_group_type_id": stock["source"], "head_count": "26",
              "sex": "ewe", "age_class": "old"},
        follow_redirects=True,
    )
    assert b"Balance editing has changed. Refresh the page" in response.data
    assert b"use Adjust Stock for count corrections" in response.data
    with app.app_context():
        assert _snapshot() == before


def test_archived_missing_and_unauthenticated_mobs_cannot_reclassify(client, app, stock):
    with app.app_context():
        db.session.get(Mob, stock["mob"]).status = "inactive"
        db.session.commit()
        before = _snapshot()
    for mob_id in (stock["mob"], "missing"):
        response = client.post(f"/mobs/{mob_id}/balances/reclassify", data=_form(stock))
        assert response.status_code == 404
    app.config["WEB_AUTH_REQUIRED"] = True
    response = client.post(f"/mobs/{stock['mob']}/balances/reclassify", data=_form(stock))
    assert response.status_code == 302
    assert "/login?next=" in response.location
    with app.app_context():
        assert _snapshot() == before


@pytest.mark.parametrize("failure_at", ["posting", "event"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_reclassification_failure_rolls_back_all_records(
    client, app, stock, monkeypatch, failure_at, error_type,
):
    with app.app_context():
        paddock = Paddock(farm_id=stock["farm"], name="History Camp", area_ha=10)
        db.session.add(paddock)
        db.session.flush()
        MovementService.move_mob(db.session.get(Mob, stock["mob"]), [{
            "paddock_id": paddock.id,
            "group_counts": [{"animal_group_type_id": stock["source"], "head_count": 100}],
        }], allocation_mode="counts")
        db.session.commit()
        before = _snapshot()

    if failure_at == "posting":
        real_post = StockService.adjust_stock
        posted = 0

        def fail_after_first_post(**kwargs):
            nonlocal posted
            posted += 1
            if posted == 2:
                raise error_type("Simulated reclassification failure")
            return real_post(**kwargs)

        monkeypatch.setattr(StockService, "adjust_stock", fail_after_first_post)
    else:
        def fail_event(**kwargs):
            raise error_type("Simulated reclassification failure")

        monkeypatch.setattr(MobEventService, "create_event", fail_event)

    if error_type is ValueError:
        response = client.post(
            f"/mobs/{stock['mob']}/balances/reclassify", data=_form(stock, sex="wether"),
            follow_redirects=True,
        )
        assert b"Simulated reclassification failure" in response.data
    else:
        with pytest.raises(RuntimeError, match="Simulated reclassification failure"):
            client.post(
                f"/mobs/{stock['mob']}/balances/reclassify", data=_form(stock, sex="wether"),
            )
    with app.app_context():
        assert _snapshot() == before
