from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingSession,
    JournalEntry,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
    RainfallRecord,
    StockLedgerEntry,
)
from app.models.movement import MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType


def _write_farm_kml(app, farm_name: str, placemark_name: str):
    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    (maps_dir / f"{farm_name}.kml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>{placemark_name}</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              25.0000,-32.0000,0 25.0100,-32.0000,0 25.0100,-32.0100,0 25.0000,-32.0100,0 25.0000,-32.0000,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
""",
        encoding="utf-8",
    )


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_create_farm_via_api(client):
    response = client.post("/api/farms", json={"name": "Farm A", "timezone": "UTC"})
    assert response.status_code == 201
    assert "id" in response.json


def test_web_dashboard_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_analytics_pages_load(client):
    response = client.get("/analytics")
    assert response.status_code == 200
    assert b"Analytics" in response.data

    response = client.get("/analytics/stock-tracking")
    assert response.status_code == 200
    assert b"Stock Tracking" in response.data

    response = client.get("/analytics/journal")
    assert response.status_code == 200
    assert b"Journal" in response.data


def test_journal_page_can_create_manual_entry(client, app):
    with app.app_context():
        farm = Farm(name="Manual Journal Farm", timezone="UTC")
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    response = client.post(
        "/analytics/journal",
        data={
            "farm_id": farm_id,
            "event_at": "2026-03-06T09:45",
            "tags": "planning,feed order",
            "description": "Ordered supplementary feed and scheduled pickup for Monday.",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        entry = JournalEntry.query.filter_by(farm_id=farm_id).first()
        assert entry is not None
        assert "planning" in entry.tags_csv
        assert "feed order" in entry.tags_csv
        assert "supplementary feed" in entry.description

    page = client.get(
        f"/analytics/journal?farm_id={farm_id}&start_date=2026-03-01&end_date=2026-03-10"
    )
    assert page.status_code == 200
    body = page.data.decode("utf-8")
    assert "Ordered supplementary feed and scheduled pickup for Monday." in body
    assert "planning" in body


def test_stock_tracking_renders_series(client, app):
    with app.app_context():
        farm = Farm(name="Analytics Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)

        mob = Mob(farm_id=farm.id, name="Analytics Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        db.session.add(
            StockLedgerEntry(
                farm_id=farm.id,
                mob_id=mob.id,
                animal_group_type_id=group.id,
                event_time=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
                event_type=StockEventType.purchase,
                quantity=12,
            )
        )
        db.session.add(
            StockLedgerEntry(
                farm_id=farm.id,
                mob_id=mob.id,
                animal_group_type_id=group.id,
                event_time=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
                event_type=StockEventType.sale,
                quantity=2,
            )
        )
        db.session.commit()

    response = client.get(
        f"/analytics/stock-tracking?farm_id={farm_id}&species=Goat&group_by=species"
    )
    assert response.status_code == 200
    assert b"Goat" in response.data
    assert b"Latest Totals" in response.data


def test_stock_tracking_reconciles_latest_to_current_balance(client, app):
    with app.app_context():
        farm = Farm(name="Reconcile Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)

        mob = Mob(farm_id=farm.id, name="Reconcile Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        # Simulate legacy/opening stock not fully represented in ledger history.
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=59,
            )
        )
        db.session.add(
            StockLedgerEntry(
                farm_id=farm.id,
                mob_id=mob.id,
                animal_group_type_id=group.id,
                event_time=datetime(2026, 3, 4, 8, 0, tzinfo=timezone.utc),
                event_type=StockEventType.adjustment_in,
                quantity=14,
            )
        )
        db.session.add(
            StockLedgerEntry(
                farm_id=farm.id,
                mob_id=mob.id,
                animal_group_type_id=group.id,
                event_time=datetime(2026, 3, 5, 8, 0, tzinfo=timezone.utc),
                event_type=StockEventType.transfer_in,
                quantity=10,
            )
        )
        db.session.commit()

    response = client.get(
        f"/analytics/stock-tracking?farm_id={farm_id}&species=Goat&breed=Angora&sex=ewe&age_class=adult"
        "&group_by=species&group_by=breed&group_by=sex&group_by=age_class"
    )
    assert response.status_code == 200

    text = response.data.decode("utf-8")
    assert "Species: Goat | Breed: Angora | Sex: ewe | Age Class: adult" in text
    assert "<td>59</td>" in text
    assert "<td>24</td>" not in text


def test_journal_aggregates_entries_with_original_and_auto_tags(client, app):
    with app.app_context():
        farm = Farm(name="Journal Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)

        mob = Mob(farm_id=farm.id, name="Journal Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        db.session.add(
            MobEvent(
                mob_id=mob.id,
                farm_id=farm.id,
                event_at=datetime(2026, 3, 5, 7, 30, tzinfo=timezone.utc),
                tags_csv="health,vaccination",
                description="Vaccinated and weighed goats.",
            )
        )
        db.session.add(
            StockLedgerEntry(
                farm_id=farm.id,
                mob_id=mob.id,
                animal_group_type_id=group.id,
                event_time=datetime(2026, 3, 5, 9, 15, tzinfo=timezone.utc),
                event_type=StockEventType.adjustment_in,
                quantity=4,
                note="Manual stock correction after counting.",
            )
        )
        movement_event = MovementEvent(
            farm_id=farm.id,
            event_time=datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc),
            event_kind=MovementEventKind.move,
            source_note="Shifted mob to lower camp.",
        )
        db.session.add(movement_event)
        db.session.flush()
        db.session.add(
            MovementEventMob(
                movement_event_id=movement_event.id,
                mob_id=mob.id,
                role=MovementRole.source,
            )
        )
        db.session.add(
            RainfallRecord(
                farm_id=farm.id,
                recorded_on=datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc).date(),
                mm=12.5,
                source="manual",
                note="Late afternoon thunderstorm.",
            )
        )
        db.session.commit()

    response = client.get(
        f"/analytics/journal?farm_id={farm_id}&start_date=2026-03-01&end_date=2026-03-06"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Vaccinated and weighed goats." in body
    assert "health" in body
    assert "Manual stock correction after counting." in body
    assert "stock" in body
    assert "Shifted mob to lower camp." in body
    assert "movement" in body
    assert "Late afternoon thunderstorm." in body
    assert "rainfall" in body

    filtered = client.get(
        f"/analytics/journal?farm_id={farm_id}&start_date=2026-03-01&end_date=2026-03-06&tag=health"
    )
    assert filtered.status_code == 200
    filtered_body = filtered.data.decode("utf-8")
    assert "Vaccinated and weighed goats." in filtered_body
    assert "Manual stock correction after counting." not in filtered_body


def test_paddock_api_includes_rest_days_and_area_per_current_lsu(client, app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        farm = Farm(name="Rest Metrics Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(farm_id=farm.id, name="Rest Camp", area_ha=15, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Rest Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=10,
            )
        )

        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=now - timedelta(days=9),
            end_at=now - timedelta(days=4),
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )
        db.session.commit()
        paddock_id = str(paddock.id)

    response = client.get(f"/api/paddocks/{paddock_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_activity_state"] == "rested"
    assert payload["current_activity_label"] == "Days Rested Continuously"
    assert payload["current_activity_days"] > 3.9
    assert payload["days_grazed_continuously"] is None
    assert payload["days_rested_continuously"] > 3.9
    assert payload["current_lsu"] == 0.0
    assert payload["paddock_ha_per_current_lsu"] is None


def test_zero_lsu_allocations_do_not_mark_paddock_as_grazed(client, app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        farm = Farm(name="Zero LSU Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(farm_id=farm.id, name="Quiet Camp", area_ha=10, grazeable_area_ha=8)
        db.session.add(paddock)
        db.session.flush()

        grazed_mob = Mob(farm_id=farm.id, name="Historic Mob", status="active")
        db.session.add(grazed_mob)
        db.session.flush()

        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        db.session.add(
            AnimalGroupBalance(
                mob_id=grazed_mob.id,
                animal_group_type_id=group.id,
                head_count=12,
            )
        )

        grazed_session = GrazingSession(
            farm_id=farm.id,
            mob_id=grazed_mob.id,
            start_at=now - timedelta(days=9),
            end_at=now - timedelta(days=4),
        )
        db.session.add(grazed_session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=grazed_session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )

        archived_mob = Mob(farm_id=farm.id, name="Deprecated Mob", status="archived")
        db.session.add(archived_mob)
        db.session.flush()

        zero_lsu_session = GrazingSession(
            farm_id=farm.id,
            mob_id=archived_mob.id,
            start_at=now - timedelta(days=2),
            end_at=None,
        )
        db.session.add(zero_lsu_session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=zero_lsu_session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )

        db.session.commit()
        paddock_id = str(paddock.id)

    response = client.get(f"/api/paddocks/{paddock_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_lsu"] == 0.0
    assert payload["current_activity_state"] == "rested"
    assert payload["current_activity_label"] == "Days Rested Continuously"
    assert payload["current_activity_days"] > 3.9
    assert payload["days_grazed_continuously"] is None
    assert payload["days_rested_continuously"] > 3.9


def test_paddock_page_and_map_data_show_grazed_days_and_area_per_current_lsu(client, app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        farm = Farm(name="Mapped Metrics Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)

        paddock = Paddock(farm_id=farm.id, name="North 1", area_ha=12, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.flush()
        paddock_id = str(paddock.id)

        mob = Mob(farm_id=farm.id, name="Map Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        db.session.add(group)
        db.session.flush()

        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=6,
            )
        )

        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=now - timedelta(days=3),
            end_at=None,
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )
        db.session.commit()

    _write_farm_kml(app, "Mapped Metrics Farm", "North 1")

    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Days Grazed Continuously" in body
    assert "Paddock Area (ha)" in body
    assert "Paddock Hectares per Current LSU" in body
    assert "Current total LSU on paddock: 6.00" in body
    assert ">12.00<" in body
    assert ">2.00<" in body

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    feature = next(item for item in payload["features"] if item["properties"]["name"] == "North 1")
    properties = feature["properties"]
    assert properties["current_activity_state"] == "grazed"
    assert properties["current_activity_label"] == "Days Grazed Continuously"
    assert properties["current_activity_days"] > 2.9
    assert properties["days_grazed_continuously"] > 2.9
    assert properties["days_rested_continuously"] is None
    assert properties["area_ha"] == 12.0
    assert properties["paddock_ha_per_current_lsu"] == 2.0
    assert properties["current_lsu"] == 6.0


def test_mob_balance_edit_reclassifies_and_records_change(client, app):
    with app.app_context():
        farm = Farm(name="Reclass Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Reclass Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        source_group = AnimalGroupType(species="Sheep", breed="Dohne", sex="ram", age_class="lamb")
        db.session.add(source_group)
        db.session.flush()

        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=source_group.id,
                head_count=12,
            )
        )
        db.session.commit()

        mob_id = str(mob.id)
        source_group_id = str(source_group.id)

    response = client.post(
        f"/mobs/{mob_id}/balances/edit",
        data={
            "source_animal_group_type_id": source_group_id,
            "sex": "wether",
            "age_class": "young",
            "head_count": "10",
            "note": "Castrated and aged up",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        source_balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=source_group_id,
        ).first()
        assert source_balance is None

        target_group = AnimalGroupType.query.filter_by(
            species="Sheep",
            breed="Dohne",
            sex="wether",
            age_class="young",
        ).first()
        assert target_group is not None

        target_balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=target_group.id,
        ).first()
        assert target_balance is not None
        assert target_balance.head_count == 10

        ledger_rows = StockLedgerEntry.query.filter_by(mob_id=mob_id).all()
        assert len(ledger_rows) == 2
        posted = {(row.event_type, str(row.animal_group_type_id), row.quantity) for row in ledger_rows}
        assert (StockEventType.adjustment_out, source_group_id, 12) in posted
        assert (StockEventType.adjustment_in, str(target_group.id), 10) in posted

        mob_event = MobEvent.query.filter_by(mob_id=mob_id).first()
        assert mob_event is not None
        assert "ram" in mob_event.description
        assert "wether" in mob_event.description
        assert "lamb" in mob_event.description
        assert "young" in mob_event.description
