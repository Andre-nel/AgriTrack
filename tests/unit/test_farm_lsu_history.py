from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as Row

import pytest
from werkzeug.datastructures import MultiDict

from app.models.stock_ledger import StockEventType
from app.models.movement import MovementEventKind, MovementRole
from app.modules.grazing.forms import parse_farm_lsu_history_filters
from app.modules.grazing.lsu_history import mob_daily_inventory
from app.modules.grazing.presenters import present_farm_lsu_history


def at(day, hour=23):
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def anchor(*, day=5, count=10, net=0, farm="a", active=True, source="baseline"):
    return Row(id=1, source=source, farm_id=farm, previous_farm_id=None, active=active,
               previous_active=None, effective_at=at(day, 0), recorded_at=at(day, 0),
               baseline_groups=[Row(animal_group_type_id="g", head_count=count, ledger_net=net)])


def posting(day, quantity, *, event_type=StockEventType.purchase, farm="a", group="g"):
    return Row(event_time=at(day, 0), event_type=event_type, farm_id=farm,
               animal_group_type_id=group, quantity=quantity, created_at=at(day, 0))


def transition(day, before="a", after="b", *, sequence=2, active=True, previous_active=True):
    return Row(id=sequence, source="move" if before != after else "status", farm_id=after,
               previous_farm_id=before, active=active, previous_active=previous_active,
               effective_at=at(day, 0), recorded_at=at(10, 0))


def calculate(states, entries=(), *, days=(1, 4, 5, 6, 8, 10), sessions=(), moves=(), mob=None):
    return mob_daily_inventory(mob or Row(farm_id="a"), states, entries, sessions, moves,
                               {"g": Decimal("1"), "h": Decimal("0.3")}, [at(day) for day in days]).values


def test_baseline_residual_and_late_backdated_posting():
    result = calculate([anchor(count=12, net=2)], [posting(2, 2), posting(4, 3)])
    assert [row[1] for row in result] == [None, None, 15, 15, 15, 15]
    assert result[0][2] == {"a"}


def test_future_entries_included_at_capture_are_subtracted_until_effective():
    result = calculate([anchor(count=15, net=15)], [posting(1, 10), posting(8, 5)])
    assert [row[1] for row in result] == [10, 10, 10, 10, 15, 15]


def test_legacy_reconciled_history_starts_at_first_ownership_evidence():
    result = calculate([anchor(count=10, net=10)], [posting(2, 10)])
    assert [row[1] for row in result] == [None, 10, 10, 10, 10, 10]


def test_legacy_negative_balance_invalidates_earlier_history_but_not_baseline():
    result = calculate([anchor(count=5, net=5)], [posting(1, 5, event_type=StockEventType.sale), posting(4, 10)])
    assert [row[1] for row in result] == [None, None, 5, 5, 5, 5]


def test_created_mob_contributes_known_zero_before_creation():
    result = calculate([anchor(count=0, net=0, source="created")], [posting(5, 10)])
    assert [row[1] for row in result] == [0, 0, 10, 10, 10, 10]


def test_created_mob_with_earlier_backdated_stock_preserves_supported_history():
    result = calculate([anchor(count=0, net=0, source="created")], [posting(2, 10)])
    assert [row[1] for row in result] == [0, 10, 10, 10, 10, 10]


def test_created_mob_with_incomplete_earlier_stock_does_not_report_false_zero():
    result = calculate([anchor(count=10, net=0, source="created")], [posting(2, 2)])
    assert [row[1] for row in result] == [0, None, 12, 12, 12, 12]


def test_whole_mob_move_changes_ownership_without_reusing_ledger_farm():
    result = calculate([anchor(), transition(6)], [posting(8, 2, farm="b")])
    assert result[2][:2] == ("a", 10)
    assert result[3][:2] == ("b", 10)
    assert result[4][:2] == ("b", 12)


def test_archive_preserves_past_livestock():
    result = calculate([anchor(), transition(6, after="a", active=False)])
    assert result[2][:2] == ("a", 10)
    assert result[3][:2] == ("a", 0)


def test_simultaneous_reclassification_applies_atomically_and_aggregates_cohorts():
    entries = [posting(6, 10, event_type=StockEventType.adjustment_out),
               posting(6, 4, group="h"), posting(6, 6, group="h")]
    result = calculate([anchor()], entries)
    assert result[3][1] == Decimal("3.0")


def test_backdated_conflicting_state_chain_marks_all_possible_farms():
    result = calculate([anchor(), transition(8, after="b"), transition(6, before="b", after="c", sequence=3)])
    assert result[2][:2] == ("a", 10)
    assert result[3][1] is None
    assert result[3][2] == {"a", "b", "c"}
    assert result[-1][1] is None


def test_backdated_transition_cannot_silently_overwrite_baseline():
    result = calculate([anchor(), transition(4)])
    assert all(value is None for _farm, value, _unknown in result)


def test_legacy_whole_mob_movement_requires_chain_consistent_with_baseline():
    movement = Row(event_time=at(3, 0), farm_id="b", event_kind=MovementEventKind.move)
    result = calculate([anchor(count=10, net=10, farm="b")], [posting(1, 10)],
                       moves=[(movement, MovementRole.source)])
    assert result[0][:2] == ("a", 10)
    assert result[1][:2] == ("b", 10)
    bad = calculate([anchor(count=10, net=10, farm="b")], [posting(1, 10)])
    assert bad[0][1] is None


def test_late_backdated_posting_does_not_rewrite_supported_legacy_ownership():
    movement = Row(event_time=at(3, 0), farm_id="b", event_kind=MovementEventKind.move)
    late = posting(2, 2, farm="b")
    late.created_at = at(6, 0)
    result = calculate([anchor(count=10, net=10, farm="b")], [posting(1, 10), late],
                       days=(1, 2, 4, 5), moves=[(movement, MovementRole.source)])
    assert [row[:2] for row in result] == [("a", 10), ("a", 12), ("b", 12), ("b", 12)]


def test_legacy_archived_mob_needs_archive_evidence():
    merge = Row(event_time=at(4, 0), farm_id="a", event_kind=MovementEventKind.merge)
    result = calculate([anchor(count=0, net=0, active=False)],
                       [posting(1, 10), posting(4, 10, event_type=StockEventType.transfer_out)],
                       moves=[(merge, MovementRole.source)])
    assert result[0][:2] == ("a", 10)
    assert result[1][:2] == ("a", 0)


def test_current_balance_mismatch_marks_history_unknown():
    mob = Row(farm_id="a", status="active", balances=[Row(animal_group_type_id="g", head_count=12)])
    result = calculate([anchor()], mob=mob)
    assert result[-1][1] is None


def test_old_absolute_count_is_absorbed_by_baseline_not_replayed():
    result = calculate([anchor()], [posting(2, 10, event_type=StockEventType.count)])
    assert result[1][1] is None
    assert result[2][1] == 10


def test_negative_running_balance_is_a_gap_and_can_recover():
    result = calculate([anchor()], [posting(6, 15, event_type=StockEventType.sale), posting(8, 8)])
    assert result[2][1] == 10
    assert result[3][1] is None
    assert result[4][1] == 3


def test_same_timestamp_state_sequence_is_deterministic():
    result = calculate([anchor(), transition(6, before="b", after="c", sequence=3), transition(6)])
    assert result[3][:2] == ("c", 10)


def test_filters_default_to_365_inclusive_utc_dates_and_all_farms():
    filters = parse_farm_lsu_history_filters(MultiDict(), [Row(id="a"), Row(id="b")], today=date(2026, 10, 12))
    assert filters.farm_ids == ("a", "b")
    assert (filters.end_date - filters.start_date).days == 364
    assert filters.split_mode == "aggregate"


@pytest.mark.parametrize("items", [
    [("farm_id", "missing")], [("farm_id", "a"), ("farm_id", "missing")], [("farm_id", "")],
    [("split_mode", "other")], [("start_date", "invalid")], [("end_date", "2027-01-01")],
    [("start_date", "2026-10-10"), ("end_date", "2026-10-09")],
])
def test_filters_reject_invalid_requests_without_broadening(items):
    with pytest.raises(ValueError):
        parse_farm_lsu_history_filters(MultiDict(items), [Row(id="a")], today=date(2026, 10, 12))


def test_split_and_aggregate_keep_metric_coverage_independent():
    report = {"labels": ["2026-01-01", "2026-01-02"], "farms": {
        "a": {"name": "A", "capacity": [None, Decimal(12)], "livestock": [Decimal(4), Decimal(6)], "current_livestock": 6},
        "b": {"name": "B", "capacity": [Decimal(4), Decimal(4)], "livestock": [Decimal(2), None], "current_livestock": 2},
    }}
    aggregate = present_farm_lsu_history(report, split_mode="aggregate")
    datasets = aggregate["panels"][0]["datasets"]
    assert datasets[0]["values"] == [None, 16]
    assert datasets[1]["values"] == [6, None]
    split = present_farm_lsu_history(report, split_mode="farm")
    assert len(split["panels"]) == 2
    assert split["y_max"] == 12 * 1.1
    assert split["closing_rows"][1]["livestock"] is None
