import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    AnimalGroupBalance, AnimalGroupType, Farm, FarmCapacityHistory, Mob,
    MobInventoryBaselineGroup, MobInventoryStateHistory, StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.modules.grazing.lsu_history import build_farm_lsu_history
from app.services.mob_inventory_service import MobInventoryService
from app.services.movement_service import MovementService


def at(day, hour=0):
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def fixtures():
    first = Farm(name="First Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=5)
    second = Farm(name="Second Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
    group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
    db.session.add_all([first, second, group])
    db.session.flush()
    mob = Mob(name="Unlocated inventory", farm_id=first.id)
    db.session.add(mob)
    db.session.flush()
    db.session.add_all([
        FarmCapacityHistory(farm_id=first.id, effective_at=at(2), recorded_at=at(2), capacity_lsu=10, source="baseline"),
        FarmCapacityHistory(farm_id=second.id, effective_at=at(3), recorded_at=at(3), capacity_lsu=4, source="baseline"),
        AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=8),
    ])
    state = MobInventoryStateHistory(mob_id=mob.id, farm_id=first.id, active=True,
                                    effective_at=at(2), recorded_at=at(2), source="baseline")
    db.session.add(state)
    db.session.flush()
    db.session.add(MobInventoryBaselineGroup(state_id=state.id, animal_group_type_id=group.id, head_count=8, ledger_net=0))
    db.session.commit()
    return first, second, mob, group


def payload(response):
    assert response.status_code == 200
    match = re.search(r'<script id="farmLsuHistoryData" type="application/json">(.*?)</script>',
                      response.text, flags=re.DOTALL)
    assert match
    return json.loads(match.group(1))


def test_route_all_farms_and_split_aggregate_equivalence(client, app):
    with app.app_context():
        first, second, _mob, _group = fixtures()
        ids = (first.id, second.id)
    query = [("farm_id", value) for value in ids] + [("start_date", "2026-01-01"), ("end_date", "2026-01-04")]
    aggregate = payload(client.get("/analytics/grazing-management/lsu-history", query_string=query))
    split = payload(client.get("/analytics/grazing-management/lsu-history", query_string=query + [("split_mode", "farm")]))
    assert aggregate["panels"][0]["datasets"][0]["values"] == [None, None, 14, 14]
    assert aggregate["panels"][0]["datasets"][1]["values"] == [None, None, 8, 8]
    assert [panel["title"] for panel in split["panels"]] == ["First Farm", "Second Farm"]
    assert sum(row["capacity"] for row in split["closing_rows"]) == aggregate["closing_rows"][0]["capacity"]
    default = payload(client.get("/analytics/grazing-management/lsu-history"))
    assert len(default["labels"]) == 365
    assert len(default["current_rows"]) == 2


def test_empty_database_and_empty_farm_render(client, app):
    empty = payload(client.get("/analytics/grazing-management/lsu-history"))
    assert empty["panels"] == []
    with app.app_context():
        farm = Farm(name="Empty", timezone="UTC", default_stocking_rate_ha_per_lsu=5)
        db.session.add(farm)
        db.session.flush()
        db.session.add(FarmCapacityHistory(farm_id=farm.id, effective_at=at(3), recorded_at=at(3), capacity_lsu=0, source="baseline"))
        db.session.commit()
    report = payload(client.get("/analytics/grazing-management/lsu-history"))
    assert report["closing_rows"] == [{"name": "All selected farms", "capacity": 0, "livestock": 0}]
    early = payload(client.get("/analytics/grazing-management/lsu-history?start_date=2026-01-01&end_date=2026-01-02"))
    assert early["closing_rows"][0]["livestock"] is None


@pytest.mark.parametrize("query", ["farm_id=unknown", "farm_id=", "split_mode=unexpected", "start_date=broken",
                                    "start_date=2026-01-02&end_date=2026-01-01", "end_date=2999-01-01"])
def test_invalid_route_filters_return_400_without_expanding_scope(client, query):
    response = client.get("/analytics/grazing-management/lsu-history?" + query)
    assert response.status_code == 400


def test_navigation_carries_farm_and_dates(client, app):
    with app.app_context():
        first, _second, _mob, _group = fixtures()
        farm_id = first.id
    response = client.get(f"/analytics/grazing-management?farm_id={farm_id}&start_date=2026-01-02&end_date=2026-01-04")
    link = re.search(r'href="([^"]+)">Farm LSU History</a>', response.text).group(1).replace("&amp;", "&")
    assert farm_id in link and "start_date=2026-01-02" in link and "end_date=2026-01-04" in link
    subpage = client.get(link)
    back_link = re.search(r'href="([^"]+)">Back To Grazing Management</a>', subpage.text).group(1)
    assert farm_id in back_link and "start_date=2026-01-02" in back_link


def test_history_get_issues_no_writes_and_queries_do_not_scale_by_day(client, app):
    with app.app_context():
        fixtures()
        statements = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement.lstrip().split(None, 1)[0].upper())

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get("/analytics/grazing-management/lsu-history?start_date=2026-01-01&end_date=2026-01-02").status_code == 200
            short_count = len(statements)
            statements.clear()
            assert client.get("/analytics/grazing-management/lsu-history?start_date=2025-01-01&end_date=2026-01-04").status_code == 200
            assert len(statements) == short_count
            assert all(statement == "SELECT" for statement in statements)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)


def test_utc_closing_balances_include_end_date_exclude_future_and_order_equal_times(app):
    with app.app_context():
        first, _second, mob, group = fixtures()
        balance = AnimalGroupBalance.query.filter_by(mob_id=mob.id).one()
        balance.head_count = 15
        db.session.add_all([
            StockLedgerEntry(farm_id=first.id, mob_id=mob.id, animal_group_type_id=group.id,
                             event_time=at(4, 20), event_type=StockEventType.purchase, quantity=3),
            StockLedgerEntry(farm_id=first.id, mob_id=mob.id, animal_group_type_id=group.id,
                             event_time=at(5, 15), event_type=StockEventType.purchase, quantity=4),
            FarmCapacityHistory(farm_id=first.id, effective_at=at(4), recorded_at=at(4), capacity_lsu=12, source="change"),
            FarmCapacityHistory(farm_id=first.id, effective_at=at(4), recorded_at=at(4), capacity_lsu=16, source="change"),
        ])
        db.session.commit()
        report = build_farm_lsu_history([first], start_date=date(2026, 1, 3), end_date=date(2026, 1, 5), now=at(5, 12))
        assert report["farms"][first.id]["livestock"] == [Decimal(8), Decimal(11), Decimal(11)]
        assert report["farms"][first.id]["current_livestock"] == Decimal(15)
        assert report["farms"][first.id]["capacity"] == [Decimal(10), Decimal(16), Decimal(16)]


def test_whole_mob_move_is_visible_when_selecting_former_farm(app):
    with app.app_context():
        first, second, mob, _group = fixtures()
        mob.farm_id = second.id
        db.session.add(MobInventoryStateHistory(mob_id=mob.id, farm_id=second.id, previous_farm_id=first.id,
                                               active=True, previous_active=True, effective_at=at(4),
                                               recorded_at=at(4), source="move"))
        db.session.commit()
        report = build_farm_lsu_history([first], start_date=date(2026, 1, 2), end_date=date(2026, 1, 5), now=at(6))
        assert report["farms"][first.id]["livestock"] == [Decimal(8), Decimal(8), Decimal(0), Decimal(0)]
        assert report["farms"][first.id]["current_livestock"] == 0


def test_real_partial_transfer_split_and_merge_preserve_farm_inventory(app):
    with app.app_context():
        first, second, source, group = fixtures()
        destination = Mob(name="Transfer destination", farm_id=second.id, status="active")
        db.session.add(destination)
        db.session.flush()
        MobInventoryService.ensure_baseline(destination, effective_at=at(2), source="created")
        MovementService.transfer_stock_between_mobs(
            source, destination, [{"animal_group_type_id": group.id, "quantity": 3}], when=at(3),
        )
        [child] = MovementService.split_mob(source, [{
            "name": "Split child", "groups": [{"animal_group_type_id": group.id, "quantity": 2}],
        }], when=at(4))
        merged = MovementService.merge_mobs([source, child], "Merged mob", when=at(5))
        db.session.commit()
        report = build_farm_lsu_history([first, second], start_date=date(2026, 1, 2),
                                       end_date=date(2026, 1, 6), now=at(7))
        assert report["farms"][first.id]["livestock"] == [8, 5, 5, 5, 5]
        assert report["farms"][second.id]["livestock"] == [0, 3, 3, 3, 3]
        assert source.status == child.status == "archived"
        assert merged.status == "active"
        assert report["farms"][first.id]["current_livestock"] == 5
