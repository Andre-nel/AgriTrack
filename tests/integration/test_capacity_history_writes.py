from decimal import Decimal

from app.extensions import db
from app.models import Farm, Paddock
from app.models.farm_capacity import FarmCapacityHistory
from app.modules.mobile_api.routes import _handle_paddock_update
from app.services.farm_capacity_service import FarmCapacityService
from app.services.farm_import_service import FarmImportService


def _capacities(farm_id):
    return [
        row.capacity_lsu
        for row in FarmCapacityHistory.query.filter_by(farm_id=farm_id)
        .order_by(FarmCapacityHistory.id).all()
    ]


def test_api_capacity_writes_noops_and_read_only_gets(client, app):
    response = client.post("/api/farms", json={"name": "API Capacity"})
    assert response.status_code == 201
    farm_id = response.json["id"]
    response = client.post("/api/paddocks", json={"farm_id": farm_id, "name": "North", "area_ha": 60})
    assert response.status_code == 201
    paddock_id = response.json["id"]
    assert _capacities(farm_id) == [Decimal("0"), Decimal("10")]

    assert client.patch(f"/api/farms/{farm_id}", json={"default_stocking_rate_ha_per_lsu": 3}).status_code == 200
    assert client.patch(f"/api/paddocks/{paddock_id}", json={"stocking_rate_ha_per_lsu_override": 6}).status_code == 200
    assert client.patch(f"/api/paddocks/{paddock_id}", json={"grazeable_area_ha": 30}).status_code == 200
    assert client.patch(f"/api/paddocks/{paddock_id}", json={"status": "inactive"}).status_code == 200
    assert _capacities(farm_id) == [Decimal(v) for v in (0, 10, 20, 10, 5, 0)]

    before = FarmCapacityHistory.query.count()
    assert client.patch(f"/api/farms/{farm_id}", json={"name": "New farm name"}).status_code == 200
    assert client.patch(f"/api/paddocks/{paddock_id}", json={"status": "inactive"}).status_code == 200
    assert client.get(f"/api/farms/{farm_id}").status_code == 200
    assert client.get(f"/api/paddocks/{paddock_id}").status_code == 200
    assert FarmCapacityHistory.query.count() == before


def test_api_rename_commits_capacity_in_same_transaction(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    farm = Farm(name="Rename", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=60)
    db.session.add(paddock)
    FarmCapacityService.record(farm, source="baseline")
    db.session.commit()
    response = client.patch(f"/api/paddocks/{paddock.id}", json={"name": "South", "area_ha": 120})
    assert response.status_code == 200
    db.session.expire_all()
    assert paddock.name == "South"
    assert paddock.area_ha == Decimal("120")
    assert _capacities(farm.id) == [Decimal("10"), Decimal("20")]


def test_api_failed_rename_rolls_back_capacity_and_inputs(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    farm = Farm(name="Rename failure", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North", area_ha=60)
    south = Paddock(farm_id=farm.id, name="South", area_ha=60)
    db.session.add_all([north, south])
    FarmCapacityService.record(farm, source="baseline")
    db.session.commit()
    response = client.patch(f"/api/paddocks/{north.id}", json={"name": "South", "area_ha": 120})
    assert response.status_code == 400
    db.session.expire_all()
    assert north.name == "North"
    assert north.area_ha == Decimal("60")
    assert _capacities(farm.id) == [Decimal("20")]


def test_web_paddock_override_and_clear_record_capacity(client, app):
    farm = Farm(name="Web overrides", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=60)
    db.session.add(paddock)
    FarmCapacityService.record(farm, source="baseline")
    db.session.commit()
    assert client.post(f"/paddocks/{paddock.id}/stocking-rate", data={"stocking_rate_ha_per_lsu_override": "3"}).status_code == 302
    assert client.post(f"/paddocks/{paddock.id}/stocking-rate", data={"stocking_rate_ha_per_lsu_override": ""}).status_code == 302
    assert _capacities(farm.id) == [Decimal("10"), Decimal("20"), Decimal("10")]


def test_import_captures_only_completed_capacity_and_skips_identical_reimport(app, tmp_path):
    kml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>North</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      25,-29,0 25.001,-29,0 25.001,-29.001,0 25,-29.001,0 25,-29,0
      </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
      <Placemark><name>South</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
      25.001,-29,0 25.002,-29,0 25.002,-29.001,0 25.001,-29.001,0 25.001,-29,0
      </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    </Document></kml>'''
    imported = FarmImportService.import_farm("Import capacity.kml", kml, "SAST", tmp_path)
    farm = db.session.get(Farm, imported["farm_id"])
    assert Paddock.query.filter_by(farm_id=farm.id).count() == 2
    rows = FarmCapacityHistory.query.filter_by(farm_id=farm.id).all()
    assert len(rows) == 1
    assert rows[0].source == "created"
    assert rows[0].capacity_lsu == FarmCapacityService.calculate(farm).quantize(Decimal("0.000001"))
    FarmImportService.import_farm("Import capacity.kml", kml, "SAST", tmp_path)
    assert FarmCapacityHistory.query.filter_by(farm_id=farm.id).count() == 1

    reduced_kml = kml.replace(b"South", b"Replacement").replace(b"25.002", b"25.003")
    FarmImportService.import_farm("Import capacity.kml", reduced_kml, "SAST", tmp_path)
    assert FarmCapacityHistory.query.filter_by(farm_id=farm.id).count() == 2
    assert Paddock.query.filter_by(farm_id=farm.id, name="South").one().status == "inactive"


def test_web_farm_creation_paddock_rate_edits_and_detail_share_capacity(client, app):
    assert client.post("/farms", data={"name": "Web capacity"}).status_code == 302
    farm = Farm.query.filter_by(name="Web capacity").one()
    assert _capacities(farm.id) == [Decimal("0")]
    assert client.post(f"/farms/{farm.id}/paddocks", data={
        "name": "North", "area_ha": "120", "grazeable_area_ha": "60",
        "stocking_rate_ha_per_lsu_override": "3",
    }).status_code == 302
    assert client.post(f"/farms/{farm.id}/paddocks", data={
        "name": "South", "area_ha": "60", "grazeable_area_ha": "0",
    }).status_code == 302
    assert client.post(f"/farms/{farm.id}/stocking-rate", data={
        "default_stocking_rate_ha_per_lsu": "12",
    }).status_code == 302
    assert _capacities(farm.id) == [Decimal(v) for v in (0, 20, 30, 25)]
    assert FarmCapacityService.calculate(farm) == Decimal("25")
    before = FarmCapacityHistory.query.count()
    response = client.get(f"/farms/{farm.id}")
    assert response.status_code == 200
    assert "<h2>Total Capacity LSU</h2><p>25.00</p>" in response.text
    assert FarmCapacityHistory.query.count() == before


def test_setup_records_one_final_capacity_including_empty_farms(client, app):
    assert client.post("/setup", data={
        "farm_name": "Setup capacity", "paddocks": "North,120,60\nSouth,30,0",
    }).status_code == 302
    farm = Farm.query.filter_by(name="Setup capacity").one()
    assert _capacities(farm.id) == [Decimal("15")]
    assert FarmCapacityHistory.query.filter_by(farm_id=farm.id).one().source == "created"
    assert client.post("/setup", data={"farm_name": "Empty setup"}).status_code == 302
    empty = Farm.query.filter_by(name="Empty setup").one()
    assert _capacities(empty.id) == [Decimal("0")]


def test_mobile_paddock_status_records_capacity_with_caller_transaction(app):
    farm = Farm(name="Mobile capacity", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    paddock = Paddock(farm_id=farm.id, name="North", area_ha=60)
    db.session.add(paddock)
    FarmCapacityService.record(farm, source="baseline")
    db.session.commit()
    _handle_paddock_update(farm, {"paddock_id": paddock.id, "status": "inactive"})
    assert _capacities(farm.id) == [Decimal("10"), Decimal("0")]
    db.session.rollback()
    assert paddock.status == "active"
    assert _capacities(farm.id) == [Decimal("10")]
    _handle_paddock_update(farm, {"paddock_id": paddock.id, "status": "inactive"})
    db.session.commit()
    assert _capacities(farm.id) == [Decimal("10"), Decimal("0")]
    _handle_paddock_update(farm, {"paddock_id": paddock.id, "notes": "No capacity change"})
    db.session.commit()
    assert _capacities(farm.id) == [Decimal("10"), Decimal("0")]
