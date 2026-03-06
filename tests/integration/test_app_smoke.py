from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, Mob, StockLedgerEntry
from app.models.stock_ledger import StockEventType


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
