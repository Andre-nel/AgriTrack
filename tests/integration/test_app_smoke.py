import json
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    FenceSection,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
    GrazingAllocationLsuBreakdownHistory,
    GrazingAllocationLsuHistory,
    GrazingSession,
    JournalEntry,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
    PaddockGate,
    RainfallRecord,
    StockLedgerEntry,
)
from app.models.movement import MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType
from app.services.gate_service import GateService
from app.services.movement_service import MovementService
from app.services.reporting_service import ReportingService


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


def _write_google_earth_farm_kml(app, farm_name: str, placemark_name: str):
    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    (maps_dir / f"{farm_name}.kml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">
  <Document id="farm-doc">
    <name>{farm_name}</name>
    <gx:CascadingStyle kml:id="style-1">
      <Style>
        <LineStyle>
          <color>ff2dc0fb</color>
        </LineStyle>
      </Style>
    </gx:CascadingStyle>
    <Placemark id="paddock-1">
      <name>{placemark_name}</name>
      <LookAt>
        <gx:fovy>35</gx:fovy>
      </LookAt>
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


def _square_ring(lon_start: float, lat_start: float, size_deg: float) -> list[tuple[float, float]]:
    return [
        (lon_start, lat_start),
        (lon_start + size_deg, lat_start),
        (lon_start + size_deg, lat_start + size_deg),
        (lon_start, lat_start + size_deg),
        (lon_start, lat_start),
    ]


def _rectangle_ring(
    lon_start: float,
    lat_start: float,
    lon_size_deg: float,
    lat_size_deg: float,
) -> list[tuple[float, float]]:
    return [
        (lon_start, lat_start),
        (lon_start + lon_size_deg, lat_start),
        (lon_start + lon_size_deg, lat_start + lat_size_deg),
        (lon_start, lat_start + lat_size_deg),
        (lon_start, lat_start),
    ]


def _ring_text(coords: list[tuple[float, float]]) -> str:
    return " ".join(f"{lon:.6f},{lat:.6f},0" for lon, lat in coords)


def _polygon_xml(
    outer_ring: list[tuple[float, float]],
    *,
    inner_rings: list[list[tuple[float, float]]] | None = None,
) -> str:
    holes_xml = "".join(
        f"""
        <innerBoundaryIs>
          <LinearRing>
            <coordinates>{_ring_text(ring)}</coordinates>
          </LinearRing>
        </innerBoundaryIs>
"""
        for ring in (inner_rings or [])
    )
    return f"""
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{_ring_text(outer_ring)}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
        {holes_xml}
      </Polygon>
"""


def _placemark_polygon_xml(
    name: str,
    polygons: list[dict[str, list[list[tuple[float, float]]] | list[tuple[float, float]]]],
) -> str:
    polygon_xml = "".join(
        _polygon_xml(
            polygon["outer"],
            inner_rings=polygon.get("inners"),
        )
        for polygon in polygons
    )
    return f"""
    <Placemark>
      <name>{name}</name>
      <MultiGeometry>
        {polygon_xml}
      </MultiGeometry>
    </Placemark>
"""


def _placemark_point_xml(name: str, lon: float, lat: float) -> str:
    return f"""
    <Placemark>
      <name>{name}</name>
      <Point>
        <coordinates>{lon:.6f},{lat:.6f},0</coordinates>
      </Point>
    </Placemark>
"""


def _kml_bytes(*placemarks: str) -> bytes:
    document = "".join(placemarks)
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    {document}
  </Document>
</kml>
"""
    ).encode("utf-8")


def _post_import_farm(
    client,
    *,
    filename: str,
    file_bytes: bytes,
    timezone: str = "SAST",
    follow_redirects: bool = False,
):
    return client.post(
        "/farms/import",
        data={
            "timezone": timezone,
            "farm_kml": (BytesIO(file_bytes), filename),
        },
        follow_redirects=follow_redirects,
    )


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_create_farm_via_api(client):
    response = client.post("/api/farms", json={"name": "Farm A", "timezone": "SAST"})
    assert response.status_code == 201
    assert "id" in response.json


def test_web_dashboard_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data
    assert b'data-base-layer="satellite"' in response.data
    assert b'data-gate-create-url="/dashboard/gates"' in response.data
    assert b'data-gate-requires-farm-selection="1"' in response.data


def test_farm_map_gate_create_uses_configured_paddocks_before_map_data_loads():
    script = Path("app/static/farm_map.js").read_text(encoding="utf-8")
    assert "let paddockOptions = configuredGatePaddockOptions.slice();" in script


def test_farm_map_gate_state_refreshes_map_data_after_update():
    script = Path("app/static/farm_map.js").read_text(encoding="utf-8")
    assert "loadMapData({ preserveView: true, userInitiated: true })" in script


def test_analytics_pages_load(client):
    response = client.get("/analytics")
    assert response.status_code == 200
    assert b"Analytics" in response.data
    assert b"Water Assets" in response.data

    response = client.get("/analytics/stock-tracking")
    assert response.status_code == 200
    assert b"Stock Tracking" in response.data

    response = client.get("/analytics/water-assets")
    assert response.status_code == 200
    assert b"Water Assets" in response.data

    response = client.get("/analytics/grazing-management")
    assert response.status_code == 200
    assert b"Grazing Management" in response.data

    response = client.get("/analytics/lsu-paddock-tracking")
    assert response.status_code == 200
    assert b"LSU Paddock Tracking" in response.data

    response = client.get("/analytics/journal")
    assert response.status_code == 200
    assert b"Journal" in response.data


def test_import_farm_page_loads_and_nav_contains_link(client):
    response = client.get("/farms/import")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Import Farm" in body
    assert "Choose KML File" in body


def test_import_farm_creates_farm_paddocks_and_map_file(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}]),
        _placemark_polygon_xml("South Camp", [{"outer": _square_ring(0.002, 0.000, 0.001)}]),
    )

    response = _post_import_farm(
        client,
        filename="Import Ranch.kml",
        file_bytes=file_bytes,
        timezone="Africa/Johannesburg",
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Import Ranch").first()
        assert farm is not None
        assert farm.timezone == "Africa/Johannesburg"
        paddocks = Paddock.query.filter_by(farm_id=farm.id).order_by(Paddock.name.asc()).all()
        assert [paddock.name for paddock in paddocks] == ["North Camp", "South Camp"]
        assert [float(paddock.area_ha) for paddock in paddocks] == [1.24, 1.24]
        assert [float(paddock.grazeable_area_ha) for paddock in paddocks] == [1.24, 1.24]
        farm_id = str(farm.id)

    map_path = Path(app.instance_path) / "maps" / "Import Ranch.kml"
    assert map_path.read_bytes() == file_bytes

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200


def test_import_farm_creates_closed_auto_gate_and_preserves_status_on_reimport(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}]),
        _placemark_polygon_xml("South Camp", [{"outer": _square_ring(0.001, 0.000, 0.001)}]),
    )

    response = _post_import_farm(client, filename="Gate Import Ranch.kml", file_bytes=file_bytes)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Gate Import Ranch").one()
        gate = PaddockGate.query.filter_by(farm_id=farm.id).one()
        fences = FenceSection.query.filter_by(farm_id=farm.id, active=True).all()
        assert gate.source == "auto"
        assert gate.status == "closed"
        assert gate.active is True
        assert float(gate.shared_boundary_length_m) > 100
        assert {fence.section_type for fence in fences} == {"boundary", "internal"}
        assert len([fence for fence in fences if fence.section_type == "boundary"]) == 2
        assert len([fence for fence in fences if fence.section_type == "internal"]) == 1
        assert all(fence.height_profile == "low" for fence in fences)
        assert all(fence.condition == "unknown" for fence in fences)
        internal_fence = next(fence for fence in fences if fence.section_type == "internal")
        internal_fence_id = str(internal_fence.id)
        gate.status = "open"
        internal_fence.condition = "bad"
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)

    response = _post_import_farm(client, filename="Gate Import Ranch.kml", file_bytes=file_bytes)
    assert response.status_code == 302

    with app.app_context():
        gate = db.session.get(PaddockGate, gate_id)
        assert gate.status == "open"
        assert gate.active is True
        internal_fence = db.session.get(FenceSection, internal_fence_id)
        assert internal_fence.condition == "bad"

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    gate_features = [
        feature for feature in payload["features"]
        if feature["properties"].get("feature_type") == "gate"
    ]
    fence_features = [
        feature for feature in payload["features"]
        if feature["properties"].get("feature_type") == "fence_section"
    ]
    assert len(gate_features) == 1
    gate_properties = gate_features[0]["properties"]
    assert gate_properties["status"] == "open"
    assert gate_properties["gate_detail_url"] == f"/farms/{farm_id}/gates/{gate_id}"
    assert gate_properties["gate_state_url"] == f"/farms/{farm_id}/gates/{gate_id}/state"
    assert gate_properties["gate_location_url"] == f"/farms/{farm_id}/gates/{gate_id}/location"
    assert len(fence_features) == 3
    assert any(feature["properties"]["condition"] == "bad" for feature in fence_features)


def test_web_gate_state_route_updates_active_grazing_allocations(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        mob = Mob(farm_id=farm.id, name="Route Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": north.id, "allocation_fraction": "1.0"}],
            destination_farm_id=str(farm.id),
            when=datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc),
        )
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
        )
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)
        north_id = str(north.id)
        south_id = str(south.id)
        mob_id = str(mob.id)

    response = client.post(
        f"/farms/{farm_id}/gates/{gate_id}/state",
        data={"status": "open", "event_time": "2026-06-12T10:00:00+00:00"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        gate = db.session.get(PaddockGate, gate_id)
        assert gate.status == "open"
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {
            str(row.paddock_id): float(row.allocation_fraction)
            for row in session.allocations
        }
        assert allocations == {north_id: 0.25, south_id: 0.75}


def test_web_gate_state_route_closes_open_gate_without_linked_stock(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
        )
        gate.status = "open"
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)

    response = client.get(f"/farms/{farm_id}/gates/{gate_id}", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json()["close_requirements"]["requires_choices"] is False

    response = client.post(f"/farms/{farm_id}/gates/{gate_id}/state", json={"status": "closed"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["gate"]["status"] == "closed"
    assert payload["moved_mob_count"] == 0

    with app.app_context():
        assert db.session.get(PaddockGate, gate_id).status == "closed"


def test_farm_map_data_gate_features_include_action_urls(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
            latitude="-32.00000000",
            longitude="25.00000000",
        )
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    gate_features = [
        feature for feature in payload["features"]
        if feature["properties"].get("feature_type") == "gate"
    ]
    assert len(gate_features) == 1
    properties = gate_features[0]["properties"]
    assert properties["gate_detail_url"] == f"/farms/{farm_id}/gates/{gate_id}"
    assert properties["gate_state_url"] == f"/farms/{farm_id}/gates/{gate_id}/state"
    assert properties["gate_location_url"] == f"/farms/{farm_id}/gates/{gate_id}/location"


def test_web_gate_state_route_accepts_map_json_open_and_close(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        mob = Mob(farm_id=farm.id, name="Map Route Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": north.id, "allocation_fraction": "1.0"}],
            destination_farm_id=str(farm.id),
            when=datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc),
        )
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
            latitude="-32.00000000",
            longitude="25.00000000",
        )
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)
        north_id = str(north.id)
        mob_id = str(mob.id)

    response = client.post(f"/farms/{farm_id}/gates/{gate_id}/state", json={"status": "open"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["gate"]["status"] == "open"
    assert payload["moved_mob_count"] == 1

    response = client.get(f"/farms/{farm_id}/gates/{gate_id}", headers={"Accept": "application/json"})
    close_requirements = response.get_json()["close_requirements"]
    assert close_requirements["requires_choices"] is True

    response = client.post(
        f"/farms/{farm_id}/gates/{gate_id}/state",
        json={
            "status": "closed",
            "closure_choices": [
                {"mob_id": mob_id, "component_paddock_id": north_id},
            ],
        },
    )
    assert response.status_code == 200
    assert response.get_json()["gate"]["status"] == "closed"

    with app.app_context():
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {
            str(row.paddock_id): float(row.allocation_fraction)
            for row in session.allocations
        }
        assert allocations == {north_id: 1.0}


def test_web_gate_location_route_moves_gate_and_map_feature(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
            latitude="-32.00000000",
            longitude="25.00000000",
        )
        gate.source = PaddockGate.SOURCE_AUTO
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)

    response = client.post(
        f"/farms/{farm_id}/gates/{gate_id}/location",
        json={"latitude": -32.123456789, "longitude": 25.987654321},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["gate"]["latitude"] == -32.12345679
    assert payload["gate"]["longitude"] == 25.98765432
    assert payload["gate"]["source"] == "manual"

    with app.app_context():
        gate = db.session.get(PaddockGate, gate_id)
        assert float(gate.latitude) == -32.12345679
        assert float(gate.longitude) == 25.98765432
        assert gate.source == PaddockGate.SOURCE_MANUAL

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    gate_features = [
        feature for feature in response.get_json()["features"]
        if feature["properties"].get("feature_type") == "gate"
    ]
    assert len(gate_features) == 1
    assert gate_features[0]["geometry"]["coordinates"] == [25.98765432, -32.12345679]


def test_web_gate_create_route_accepts_map_json_and_returns_feature(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)
        south_id = str(south.id)

    response = client.post(
        f"/farms/{farm_id}/gates",
        json={
            "paddock_a_id": north_id,
            "paddock_b_id": south_id,
            "latitude": -32.22222222,
            "longitude": 25.33333333,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["gate"]["status"] == "closed"
    assert payload["gate"]["source"] == "manual"
    assert payload["gate"]["latitude"] == -32.22222222
    assert payload["gate"]["longitude"] == 25.33333333
    first_gate_id = payload["gate"]["id"]

    response = client.post(
        f"/farms/{farm_id}/gates",
        json={
            "paddock_a_id": north_id,
            "paddock_b_id": south_id,
            "latitude": -32.44444444,
            "longitude": 25.55555555,
        },
    )
    assert response.status_code == 200
    second_payload = response.get_json()
    assert second_payload["gate"]["id"] != first_gate_id
    assert second_payload["gate"]["paddock_a_id"] == payload["gate"]["paddock_a_id"]
    assert second_payload["gate"]["paddock_b_id"] == payload["gate"]["paddock_b_id"]

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    gate_features = [
        feature for feature in response.get_json()["features"]
        if feature["properties"].get("feature_type") == "gate"
    ]
    assert len(gate_features) == 2
    assert {feature["properties"]["gate_id"] for feature in gate_features} == {
        first_gate_id,
        second_payload["gate"]["id"],
    }
    assert {tuple(feature["geometry"]["coordinates"]) for feature in gate_features} == {
        (25.33333333, -32.22222222),
        (25.55555555, -32.44444444),
    }


def test_web_gate_routes_allow_cross_farm_connected_camps(client, app):
    with app.app_context():
        source_farm = Farm(name="Cross Gate Source", timezone="SAST", active=True)
        neighbor_farm = Farm(name="Cross Gate Neighbor", timezone="SAST", active=True)
        db.session.add_all([source_farm, neighbor_farm])
        db.session.flush()
        source_camp = Paddock(
            farm_id=source_farm.id,
            name="Source Gate Camp",
            area_ha=10,
            grazeable_area_ha=10,
            status="active",
        )
        neighbor_camp = Paddock(
            farm_id=neighbor_farm.id,
            name="Neighbor Gate Camp",
            area_ha=12,
            grazeable_area_ha=12,
            status="active",
        )
        db.session.add_all([source_camp, neighbor_camp])
        db.session.flush()
        mob = Mob(farm_id=neighbor_farm.id, name="Cross Gate Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": neighbor_camp.id, "allocation_fraction": "1.0"}],
            destination_farm_id=str(neighbor_farm.id),
            when=datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc),
        )
        db.session.commit()
        source_farm_id = str(source_farm.id)
        neighbor_farm_id = str(neighbor_farm.id)
        source_camp_id = str(source_camp.id)
        neighbor_camp_id = str(neighbor_camp.id)
        mob_id = str(mob.id)

    response = client.post(
        f"/farms/{source_farm_id}/gates",
        json={
            "paddock_a_id": source_camp_id,
            "paddock_b_id": neighbor_camp_id,
            "latitude": -32.33333333,
            "longitude": 25.44444444,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    gate_id = payload["gate"]["id"]
    assert set(payload["gate"]["paddock_labels"]) == {
        "Cross Gate Source: Source Gate Camp",
        "Cross Gate Neighbor: Neighbor Gate Camp",
    }

    response = client.get(f"/farms/{neighbor_farm_id}/gates")
    assert response.status_code == 200
    assert b"Cross Gate Source: Source Gate Camp" in response.data
    assert b"Neighbor Gate Camp" in response.data

    response = client.get(f"/farms/{neighbor_farm_id}/gates/{gate_id}", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json()["gate"]["id"] == gate_id

    response = client.post(f"/farms/{neighbor_farm_id}/gates/{gate_id}/state", json={"status": "open"})
    assert response.status_code == 200
    assert response.get_json()["moved_mob_count"] == 1

    with app.app_context():
        assert db.session.get(PaddockGate, gate_id).status == "open"
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {
            str(row.paddock_id): float(row.allocation_fraction)
            for row in session.allocations
        }
        assert allocations == {source_camp_id: 0.4545, neighbor_camp_id: 0.5455}


def test_dashboard_gate_create_route_requires_farm_selection(client, app):
    with app.app_context():
        source_farm = Farm(name="Dashboard Gate Source", timezone="SAST", active=True)
        neighbor_farm = Farm(name="Dashboard Gate Neighbor", timezone="SAST", active=True)
        db.session.add_all([source_farm, neighbor_farm])
        db.session.flush()
        source_camp = Paddock(
            farm_id=source_farm.id,
            name="Dashboard Source Camp",
            area_ha=10,
            grazeable_area_ha=10,
            status="active",
        )
        neighbor_camp = Paddock(
            farm_id=neighbor_farm.id,
            name="Dashboard Neighbor Camp",
            area_ha=12,
            grazeable_area_ha=12,
            status="active",
        )
        db.session.add_all([source_camp, neighbor_camp])
        db.session.commit()
        source_farm_id = str(source_farm.id)
        source_camp_id = str(source_camp.id)
        neighbor_camp_id = str(neighbor_camp.id)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"Dashboard Gate Source: Dashboard Source Camp" in dashboard.data
    assert b"Dashboard Gate Neighbor" in dashboard.data

    missing_farm = client.post(
        "/dashboard/gates",
        json={"paddock_a_id": source_camp_id, "paddock_b_id": neighbor_camp_id},
    )
    assert missing_farm.status_code == 400

    response = client.post(
        "/dashboard/gates",
        json={
            "farm_id": source_farm_id,
            "paddock_a_id": source_camp_id,
            "paddock_b_id": neighbor_camp_id,
            "latitude": -32.66666666,
            "longitude": 25.77777777,
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    gate = payload["gate"]
    assert gate["farm_id"] == source_farm_id
    assert gate["gate_detail_url"] == f"/farms/{source_farm_id}/gates/{gate['id']}"
    assert gate["gate_state_url"] == f"/farms/{source_farm_id}/gates/{gate['id']}/state"
    assert gate["gate_location_url"] == f"/farms/{source_farm_id}/gates/{gate['id']}/location"
    assert set(gate["paddock_labels"]) == {
        "Dashboard Gate Source: Dashboard Source Camp",
        "Dashboard Gate Neighbor: Dashboard Neighbor Camp",
    }


def test_dashboard_map_gate_state_url_redistributes_cross_farm_stock(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        source_farm = Farm(name="Z Combined Gate Source", timezone="SAST", active=True)
        neighbor_farm = Farm(name="A Combined Gate Neighbor", timezone="SAST", active=True)
        db.session.add_all([source_farm, neighbor_farm])
        db.session.flush()
        source_camp = Paddock(
            farm_id=source_farm.id,
            name="Wear Kamp",
            area_ha=10,
            grazeable_area_ha=10,
            status="active",
        )
        neighbor_camp = Paddock(
            farm_id=neighbor_farm.id,
            name="Flakte",
            area_ha=30,
            grazeable_area_ha=30,
            status="active",
        )
        db.session.add_all([source_camp, neighbor_camp])
        db.session.flush()
        mob = Mob(farm_id=neighbor_farm.id, name="Combined Gate Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": neighbor_camp.id, "allocation_fraction": "1.0"}],
            destination_farm_id=str(neighbor_farm.id),
            when=datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc),
        )
        gate = GateService.create_manual_gate(
            farm_id=str(source_farm.id),
            paddock_a_id=str(source_camp.id),
            paddock_b_id=str(neighbor_camp.id),
            latitude="-32.11111111",
            longitude="25.22222222",
        )
        db.session.commit()
        source_farm_id = str(source_farm.id)
        neighbor_farm_id = str(neighbor_farm.id)
        source_camp_id = str(source_camp.id)
        neighbor_camp_id = str(neighbor_camp.id)
        mob_id = str(mob.id)
        gate_id = str(gate.id)
        _write_farm_kml(app, source_farm.name, source_camp.name)
        _write_farm_kml(app, neighbor_farm.name, neighbor_camp.name)

    map_response = client.get("/dashboard/map-data")
    assert map_response.status_code == 200
    gate_features = [
        feature
        for feature in map_response.get_json()["features"]
        if feature["properties"].get("gate_id") == gate_id
    ]
    assert len(gate_features) == 1
    gate_state_url = gate_features[0]["properties"]["gate_state_url"]
    assert gate_state_url == f"/farms/{neighbor_farm_id}/gates/{gate_id}/state"

    response = client.post(gate_state_url, json={"status": "open"})
    assert response.status_code == 200
    assert response.get_json()["moved_mob_count"] == 1

    with app.app_context():
        assert db.session.get(PaddockGate, gate_id).status == "open"
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {
            str(row.paddock_id): float(row.allocation_fraction)
            for row in session.allocations
        }
        assert allocations == {source_camp_id: 0.25, neighbor_camp_id: 0.75}
        assert str(session.farm_id) == neighbor_farm_id
        assert str(db.session.get(Mob, mob_id).farm_id) == neighbor_farm_id
        assert source_farm_id != neighbor_farm_id


def test_map_data_counts_cross_farm_allocations_by_paddock_farm(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        source_farm = Farm(name="Map Source Farm", timezone="SAST", active=True)
        neighbor_farm = Farm(name="Map Neighbor Farm", timezone="SAST", active=True)
        db.session.add_all([source_farm, neighbor_farm])
        db.session.flush()
        wear_kamp = Paddock(
            farm_id=source_farm.id,
            name="Wear Kamp",
            area_ha=10,
            grazeable_area_ha=10,
            status="active",
        )
        flakte = Paddock(
            farm_id=neighbor_farm.id,
            name="Flakte",
            area_ha=30,
            grazeable_area_ha=30,
            status="active",
        )
        db.session.add_all([wear_kamp, flakte])
        cattle = AnimalGroupType(species="Cattle", breed="Afrikaner", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Dohne", sex="ewe", age_class="adult")
        goats = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        cattle_mob = Mob(farm_id=source_farm.id, name="Source Cattle", status="active")
        sheep_mob = Mob(farm_id=neighbor_farm.id, name="Neighbor Sheep", status="active")
        goat_mob = Mob(farm_id=neighbor_farm.id, name="Neighbor Goats", status="active")
        db.session.add_all([cattle, sheep, goats, cattle_mob, sheep_mob, goat_mob])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=cattle_mob.id, animal_group_type_id=cattle.id, head_count=26),
                AnimalGroupBalance(mob_id=sheep_mob.id, animal_group_type_id=sheep.id, head_count=100),
                AnimalGroupBalance(mob_id=goat_mob.id, animal_group_type_id=goats.id, head_count=50),
            ]
        )
        for mob in [cattle_mob, sheep_mob, goat_mob]:
            session = GrazingSession(
                farm_id=mob.farm_id,
                mob_id=mob.id,
                start_at=datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc),
                end_at=None,
            )
            db.session.add(session)
            db.session.flush()
            db.session.add(
                GrazingAllocation(
                    grazing_session_id=session.id,
                    paddock_id=wear_kamp.id,
                    allocation_fraction="0.5",
                )
            )
        db.session.commit()
        source_farm_id = str(source_farm.id)
        wear_kamp_id = str(wear_kamp.id)
        _write_farm_kml(app, source_farm.name, wear_kamp.name)
        _write_farm_kml(app, neighbor_farm.name, flakte.name)

    def wear_kamp_properties(payload):
        return next(
            feature["properties"]
            for feature in payload["features"]
            if feature["properties"].get("paddock_id") == wear_kamp_id
        )

    farm_response = client.get(f"/farms/{source_farm_id}/map-data")
    assert farm_response.status_code == 200
    farm_properties = wear_kamp_properties(farm_response.get_json())
    assert {row["mob_name"] for row in farm_properties["mobs"]} == {
        "Source Cattle",
        "Neighbor Sheep",
        "Neighbor Goats",
    }
    assert {
        row["species"]: row["head"]
        for row in farm_properties["species_heads"]
    } == {"Cattle": 13.0, "Goat": 25.0, "Sheep": 50.0}
    assert farm_properties["current_lsu"] > 20.0

    dashboard_response = client.get("/dashboard/map-data")
    assert dashboard_response.status_code == 200
    dashboard_properties = wear_kamp_properties(dashboard_response.get_json())
    assert {row["mob_name"] for row in dashboard_properties["mobs"]} == {
        "Source Cattle",
        "Neighbor Sheep",
        "Neighbor Goats",
    }
    assert {
        row["species"]: row["head"]
        for row in dashboard_properties["species_heads"]
    } == {"Cattle": 13.0, "Goat": 25.0, "Sheep": 50.0}


def test_farm_gates_page_supports_update_delete_and_json_detail(client, app):
    with app.app_context():
        farm, (north, south) = _farm_with_two_paddocks_for_gate_route()
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
            latitude="-32.10000000",
            longitude="25.10000000",
        )
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)
        north_id = str(north.id)
        south_id = str(south.id)

    response = client.get(f"/farms/{farm_id}/gates")
    assert response.status_code == 200
    assert b"Gate Register" in response.data

    response = client.get(f"/farms/{farm_id}/gates/{gate_id}", headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.get_json()["gate"]["id"] == gate_id

    response = client.get(f"/farms/{farm_id}/gates/{gate_id}")
    assert response.status_code == 200
    assert b"Edit Gate" in response.data

    response = client.get(f"/farms/{farm_id}/gates/{gate_id}/edit")
    assert response.status_code == 200
    assert b"Edit Gate" in response.data

    response = client.post(
        f"/farms/{farm_id}/gates/{gate_id}",
        data={
            "paddock_a_id": north_id,
            "paddock_b_id": south_id,
            "latitude": "-32.76543210",
            "longitude": "25.12345678",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        gate = db.session.get(PaddockGate, gate_id)
        assert float(gate.latitude) == -32.76543210
        assert float(gate.longitude) == 25.12345678

    response = client.post(
        f"/farms/{farm_id}/gates/{gate_id}/delete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(PaddockGate, gate_id) is None


def _farm_with_two_paddocks_for_gate_route():
    farm = Farm(name="Gate Route Farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North", area_ha=10, grazeable_area_ha=10)
    south = Paddock(farm_id=farm.id, name="South", area_ha=30, grazeable_area_ha=30)
    db.session.add_all([north, south])
    db.session.flush()
    return farm, (north, south)


def test_import_farm_skips_boundary_placemark_matching_farm_name(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _placemark_polygon_xml("Boundary Farm", [{"outer": _square_ring(0.000, 0.000, 0.004)}]),
        _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.005, 0.000, 0.001)}]),
        _placemark_polygon_xml("South Camp", [{"outer": _square_ring(0.007, 0.000, 0.001)}]),
    )

    response = _post_import_farm(client, filename="Boundary Farm.kml", file_bytes=file_bytes)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Boundary Farm").first()
        assert farm is not None
        paddocks = Paddock.query.filter_by(farm_id=farm.id).order_by(Paddock.name.asc()).all()
        assert [paddock.name for paddock in paddocks] == ["North Camp", "South Camp"]
        farm_id = str(farm.id)

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    assert any(feature["properties"]["feature_type"] == "farm_boundary" for feature in payload["features"])


def test_import_farm_updates_existing_farm_and_overwrites_map_file(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Conflict Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        db.session.add(
            Paddock(
                farm_id=farm.id,
                name="North Camp",
                area_ha=5,
                grazeable_area_ha=5,
                status="inactive",
            )
        )
        db.session.add(
            Paddock(
                farm_id=farm.id,
                name="Legacy Camp",
                area_ha=7,
                grazeable_area_ha=7,
            )
        )
        db.session.commit()
        farm_id = str(farm.id)

    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    existing_path = maps_dir / "Conflict Farm.kml"
    existing_path.write_bytes(b"old-map")

    response = _post_import_farm(
        client,
        filename="Conflict Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}]),
            _placemark_polygon_xml("South Camp", [{"outer": _square_ring(0.002, 0.000, 0.001)}]),
        ),
        timezone="Africa/Johannesburg",
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(id=farm_id).first()
        assert farm is not None
        assert farm.timezone == "Africa/Johannesburg"
        paddocks = Paddock.query.filter_by(farm_id=farm.id).order_by(Paddock.name.asc()).all()
        assert [paddock.name for paddock in paddocks] == ["Legacy Camp", "North Camp", "South Camp"]
        north = next(paddock for paddock in paddocks if paddock.name == "North Camp")
        south = next(paddock for paddock in paddocks if paddock.name == "South Camp")
        legacy = next(paddock for paddock in paddocks if paddock.name == "Legacy Camp")
        assert float(north.area_ha) == 1.24
        assert float(north.grazeable_area_ha) == 1.24
        assert north.status == "active"
        assert float(south.area_ha) == 1.24
        assert float(south.grazeable_area_ha) == 1.24
        assert float(legacy.area_ha) == 7.0
        assert legacy.status == "inactive"

    assert existing_path.read_bytes() != b"old-map"
    assert b"South Camp" in existing_path.read_bytes()

    farm_page = client.get(f"/farms/{farm_id}")
    assert farm_page.status_code == 200
    farm_body = farm_page.data.decode("utf-8")
    assert "North Camp" in farm_body
    assert "South Camp" in farm_body
    assert "Legacy Camp" not in farm_body


def test_import_farm_transfers_missing_paddock_history_by_overlap_ratio(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Split Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)

        source = Paddock(
            farm_id=farm.id,
            name="Split Source",
            area_ha=2.47,
            grazeable_area_ha=2.47,
        )
        db.session.add(source)
        db.session.flush()
        source_id = str(source.id)

        mob = Mob(farm_id=farm.id, name="Split Mob", status="active")
        db.session.add(mob)
        db.session.flush()

        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
            end_at=None,
        )
        db.session.add(session)
        db.session.flush()
        session_id = str(session.id)

        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=source.id,
            allocation_fraction=1,
        )
        db.session.add(allocation)
        db.session.flush()

        db.session.add(
            GrazingAllocationLsuHistory(
                farm_id=farm.id,
                mob_id=mob.id,
                paddock_id=source.id,
                grazing_session_id=session.id,
                grazing_allocation_id=allocation.id,
                effective_from=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
                effective_to=None,
                allocation_fraction=1,
                mob_total_lsu=8,
                allocated_lsu=8,
                source="live",
            )
        )
        db.session.commit()

    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    existing_path = maps_dir / "Split Farm.kml"
    existing_path.write_bytes(
        _kml_bytes(
            _placemark_polygon_xml(
                "Split Source",
                [{"outer": _rectangle_ring(0.000, 0.000, 0.002, 0.001)}],
            )
        )
    )

    response = _post_import_farm(
        client,
        filename="Split Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("West Target", [{"outer": _square_ring(0.000, 0.000, 0.001)}]),
            _placemark_polygon_xml("East Target", [{"outer": _square_ring(0.001, 0.000, 0.001)}]),
        ),
    )
    assert response.status_code == 302

    with app.app_context():
        source = Paddock.query.filter_by(id=source_id).first()
        assert source is not None
        assert source.status == "inactive"

        active_paddocks = (
            Paddock.query.filter_by(farm_id=farm_id, status="active").order_by(Paddock.name.asc()).all()
        )
        assert [paddock.name for paddock in active_paddocks] == ["East Target", "West Target"]

        allocations = (
            GrazingAllocation.query.join(Paddock)
            .filter(GrazingAllocation.grazing_session_id == session_id)
            .order_by(Paddock.name.asc())
            .all()
        )
        assert [allocation.paddock.name for allocation in allocations] == ["East Target", "West Target"]
        assert [float(allocation.allocation_fraction) for allocation in allocations] == [0.5, 0.5]

        history_rows = (
            GrazingAllocationLsuHistory.query.join(Paddock)
            .filter(GrazingAllocationLsuHistory.grazing_session_id == session_id)
            .order_by(Paddock.name.asc())
            .all()
        )
        assert [row.paddock.name for row in history_rows] == ["East Target", "West Target"]
        assert [float(row.allocation_fraction) for row in history_rows] == [0.5, 0.5]
        assert [float(row.allocated_lsu) for row in history_rows] == [4.0, 4.0]

    farm_page = client.get(f"/farms/{farm_id}")
    assert farm_page.status_code == 200
    farm_body = farm_page.data.decode("utf-8")
    assert "West Target" in farm_body
    assert "East Target" in farm_body
    assert "Split Source" not in farm_body

def test_count_based_mob_move_persists_group_assignments_and_lsu_history(client, app):
    with app.app_context():
        farm = Farm(name="Count Allocation Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Counts", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Counts", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Mixed Count Mob", status="active")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([north, south, mob, cattle, sheep])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5),
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=sheep.id, head_count=12),
            ]
        )
        db.session.flush()

        MovementService.move_mob(
            mob=mob,
            destination_farm_id=str(farm.id),
            when=datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc),
            allocation_mode="counts",
            allocations=[
                {
                    "paddock_id": str(north.id),
                    "group_counts": [{"animal_group_type_id": str(sheep.id), "head_count": 12}],
                },
                {
                    "paddock_id": str(south.id),
                    "group_counts": [{"animal_group_type_id": str(cattle.id), "head_count": 5}],
                },
            ],
        )
        db.session.commit()

        session = GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()
        allocations = {str(row.paddock_id): row for row in session.allocations}
        assert float(allocations[str(north.id)].allocation_fraction) == 0.2857
        assert float(allocations[str(south.id)].allocation_fraction) == 0.7143

        assignments = GrazingAllocationGroupAssignment.query.all()
        assert {(str(row.animal_group_type_id), int(row.head_count)) for row in assignments} == {
            (str(sheep.id), 12),
            (str(cattle.id), 5),
        }

        aggregate_history = {
            str(row.paddock_id): float(row.allocated_lsu)
            for row in GrazingAllocationLsuHistory.query.filter_by(grazing_session_id=session.id).all()
        }
        assert aggregate_history[str(north.id)] == 2.0
        assert aggregate_history[str(south.id)] == 5.0

        breakdown = {
            (str(row.paddock_id), str(row.animal_group_type_id)): float(row.allocated_head_count)
            for row in GrazingAllocationLsuBreakdownHistory.query.filter_by(grazing_session_id=session.id).all()
        }
        assert breakdown[(str(north.id), str(sheep.id))] == 12.0
        assert breakdown[(str(south.id), str(cattle.id))] == 5.0

        north_stock = ReportingService.paddock_current_stock(str(north.id))
        south_stock = ReportingService.paddock_current_stock(str(south.id))
        assert north_stock[str(sheep.id)] == 12.0
        assert south_stock[str(cattle.id)] == 5.0


def test_web_count_mob_move_allows_destination_rows_on_different_farms(client, app):
    with app.app_context():
        source_farm = Farm(name="Count Row Source Farm", timezone="SAST")
        neighbor_farm = Farm(name="Count Row Neighbor Farm", timezone="SAST")
        db.session.add_all([source_farm, neighbor_farm])
        db.session.flush()
        home = Paddock(
            farm_id=source_farm.id,
            name="Home Count Row",
            area_ha=10,
            grazeable_area_ha=10,
        )
        away = Paddock(
            farm_id=neighbor_farm.id,
            name="Away Count Row",
            area_ha=10,
            grazeable_area_ha=10,
        )
        mob = Mob(farm_id=source_farm.id, name="Cross Farm Count Mob", status="active")
        group = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        db.session.add_all([home, away, mob, group])
        db.session.flush()
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=10))
        db.session.commit()

        mob_id = str(mob.id)
        source_farm_id = str(source_farm.id)
        neighbor_farm_id = str(neighbor_farm.id)
        home_id = str(home.id)
        away_id = str(away.id)
        group_id = str(group.id)

    response = client.post(
        f"/mobs/{mob_id}/move",
        data={
            "allocation_mode": "counts",
            "count_farm_id": [source_farm_id, neighbor_farm_id],
            "count_paddock_id": [home_id, away_id],
            "count_group_id": [group_id, group_id],
            "count_head_count": ["4", "6"],
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Mob moved" in response.data

    with app.app_context():
        mob = db.session.get(Mob, mob_id)
        assert str(mob.farm_id) == source_farm_id
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {str(row.paddock_id): float(row.allocation_fraction) for row in session.allocations}
        assert allocations == {home_id: 0.4, away_id: 0.6}

        history_farms = {
            str(row.paddock_id): str(row.farm_id)
            for row in GrazingAllocationLsuHistory.query.filter_by(grazing_session_id=session.id).all()
        }
        assert history_farms == {home_id: source_farm_id, away_id: neighbor_farm_id}


def test_count_based_mob_move_merges_duplicate_destination_paddocks(client, app):
    with app.app_context():
        farm = Farm(name="Count Merge Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Merge", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Merge", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Merged Count Mob", status="active")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([north, south, mob, cattle, sheep])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5),
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=sheep.id, head_count=12),
            ]
        )
        db.session.flush()

        MovementService.move_mob(
            mob=mob,
            destination_farm_id=str(farm.id),
            when=datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc),
            allocation_mode="counts",
            allocations=[
                {
                    "paddock_id": str(north.id),
                    "group_counts": [{"animal_group_type_id": str(sheep.id), "head_count": 5}],
                },
                {
                    "paddock_id": str(north.id),
                    "group_counts": [{"animal_group_type_id": str(sheep.id), "head_count": 7}],
                },
                {
                    "paddock_id": str(south.id),
                    "group_counts": [{"animal_group_type_id": str(cattle.id), "head_count": 5}],
                },
            ],
        )
        db.session.commit()

        session = GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()
        allocations = {str(row.paddock_id): row for row in session.allocations}
        assert set(allocations) == {str(north.id), str(south.id)}
        assert float(allocations[str(north.id)].allocation_fraction) == 0.2857
        assert float(allocations[str(south.id)].allocation_fraction) == 0.7143

        assignments = {
            (str(row.grazing_allocation.paddock_id), str(row.animal_group_type_id)): int(row.head_count)
            for row in GrazingAllocationGroupAssignment.query.all()
        }
        assert assignments == {
            (str(north.id), str(sheep.id)): 12,
            (str(south.id), str(cattle.id)): 5,
        }


def test_percentage_mob_move_still_rejects_duplicate_destination_paddocks(client, app):
    with app.app_context():
        farm = Farm(name="Percentage Duplicate Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="One Paddock", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Percentage Duplicate Mob", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()

        try:
            MovementService.move_mob(
                mob=mob,
                destination_farm_id=str(farm.id),
                allocation_mode="percentage",
                allocations=[
                    {"paddock_id": str(paddock.id), "allocation_fraction": "0.5"},
                    {"paddock_id": str(paddock.id), "allocation_fraction": "0.5"},
                ],
            )
        except ValueError as exc:
            assert "Duplicate paddock rows are not allowed" in str(exc)
        else:
            raise AssertionError("Duplicate percentage destination rows should fail")


def test_count_based_mob_move_requires_whole_mob_coverage(client, app):
    with app.app_context():
        farm = Farm(name="Count Validation Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="Only Counts", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Partial Count Mob", status="active")
        group = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        db.session.add_all([paddock, mob, group])
        db.session.flush()
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=8))
        db.session.flush()

        try:
            MovementService.move_mob(
                mob=mob,
                destination_farm_id=str(farm.id),
                allocation_mode="counts",
                allocations=[
                    {
                        "paddock_id": str(paddock.id),
                        "group_counts": [{"animal_group_type_id": str(group.id), "head_count": 7}],
                    }
                ],
            )
        except ValueError as exc:
            assert "cover the whole mob" in str(exc)
        else:
            raise AssertionError("Partial count allocation should fail")


def test_farm_detail_marks_unallocated_mobs_in_red(client, app):
    with app.app_context():
        farm = Farm(name="Unallocated Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Angora Groot Ramme", status="active")
        db.session.add(mob)
        db.session.commit()

        farm_id = str(farm.id)

    response = client.get(f"/farms/{farm_id}")
    assert response.status_code == 200

    body = response.data.decode("utf-8")
    assert "Angora Groot Ramme" in body
    assert "(Not Located)" in body
    assert 'class="mob-unallocated"' in body
    assert 'data-base-layer="satellite"' in body


def test_import_farm_rejects_existing_map_file(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    existing_path = maps_dir / "Mapped Farm.kml"
    existing_path.write_bytes(b"existing-map")

    response = _post_import_farm(
        client,
        filename="Mapped Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}])
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "A map file for Mapped Farm already exists" in response.data.decode("utf-8")
    assert existing_path.read_bytes() == b"existing-map"

    with app.app_context():
        assert Farm.query.filter_by(name="Mapped Farm").first() is None


def test_import_farm_rejects_empty_file(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Empty Farm.kml",
        file_bytes=b"",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Uploaded KML file is empty" in response.data.decode("utf-8")


def test_import_farm_rejects_invalid_xml(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Broken Farm.kml",
        file_bytes=b"<kml><Document>",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Unable to parse the uploaded KML file" in response.data.decode("utf-8")


def test_import_farm_rejects_non_kml_extension(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Wrong Format.txt",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}])
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Farm import requires a .kml file" in response.data.decode("utf-8")


def test_import_farm_rejects_duplicate_paddock_names(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Duplicate Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("North Camp", [{"outer": _square_ring(0.000, 0.000, 0.001)}]),
            _placemark_polygon_xml(" north   camp ", [{"outer": _square_ring(0.002, 0.000, 0.001)}]),
        ),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Duplicate paddock names were found in the uploaded KML" in response.data.decode("utf-8")


def test_import_farm_rejects_kml_without_paddock_polygons(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Point Farm.kml",
        file_bytes=_kml_bytes(_placemark_point_xml("Water Point", 0.000, 0.000)),
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "No paddock polygons were found in the uploaded KML" in response.data.decode("utf-8")


def test_import_farm_imports_multipolygon_paddock_area(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Split Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml(
                "Split Camp",
                [
                    {"outer": _square_ring(0.000, 0.000, 0.001)},
                    {"outer": _square_ring(0.002, 0.000, 0.001)},
                ],
            )
        ),
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Split Farm").first()
        assert farm is not None
        paddock = Paddock.query.filter_by(farm_id=farm.id, name="Split Camp").first()
        assert paddock is not None
        assert float(paddock.area_ha) == 2.47
        assert float(paddock.grazeable_area_ha) == 2.47


def test_import_farm_imports_polygon_with_hole_area(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    response = _post_import_farm(
        client,
        filename="Hole Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml(
                "Holed Camp",
                [
                    {
                        "outer": _square_ring(0.000, 0.000, 0.002),
                        "inners": [_square_ring(0.0005, 0.0005, 0.001)],
                    }
                ],
            )
        ),
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Hole Farm").first()
        assert farm is not None
        paddock = Paddock.query.filter_by(farm_id=farm.id, name="Holed Camp").first()
        assert paddock is not None
        assert float(paddock.area_ha) == 3.71
        assert float(paddock.grazeable_area_ha) == 3.71


def test_journal_page_can_create_manual_entry(client, app):
    with app.app_context():
        farm = Farm(name="Manual Journal Farm", timezone="SAST")
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
        farm = Farm(name="Analytics Farm", timezone="SAST")
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


def test_stock_tracking_allows_multiple_farm_filters(client, app):
    with app.app_context():
        farms = [
            Farm(name="Multi Stock North", timezone="SAST"),
            Farm(name="Multi Stock South", timezone="SAST"),
            Farm(name="Multi Stock Outside", timezone="SAST"),
        ]
        db.session.add_all(farms)
        db.session.flush()
        selected_farm_ids = [str(farms[0].id), str(farms[1].id)]

        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()

        quantities = [5, 7, 11]
        for farm, quantity in zip(farms, quantities):
            mob = Mob(farm_id=farm.id, name=f"{farm.name} Mob", status="active")
            db.session.add(mob)
            db.session.flush()
            db.session.add(
                StockLedgerEntry(
                    farm_id=farm.id,
                    mob_id=mob.id,
                    animal_group_type_id=group.id,
                    event_time=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
                    event_type=StockEventType.purchase,
                    quantity=quantity,
                )
            )
        db.session.commit()

    response = client.get(
        "/analytics/stock-tracking"
        f"?farm_id={selected_farm_ids[0]}&farm_id={selected_farm_ids[1]}"
        "&start_date=2026-01-01&end_date=2026-01-01&group_by=farm"
    )
    assert response.status_code == 200

    text = response.data.decode("utf-8")
    assert 'select name="farm_id" multiple' in text

    match = re.search(
        r'<script id="stockTrackingChartData" type="application/json">(.*?)</script>',
        text,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert sorted(dataset["label"] for dataset in payload["datasets"]) == [
        "Multi Stock North",
        "Multi Stock South",
    ]


def test_stock_tracking_reconciles_latest_to_current_balance(client, app):
    with app.app_context():
        farm = Farm(name="Reconcile Farm", timezone="SAST")
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
        farm = Farm(name="Journal Farm", timezone="SAST")
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
        farm = Farm(name="Rest Metrics Farm", timezone="SAST")
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
        farm = Farm(name="Zero LSU Farm", timezone="SAST")
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
        farm = Farm(name="Mapped Metrics Farm", timezone="SAST")
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
        mob_id = str(mob.id)

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
    assert properties["mobs"][0]["mob_id"] == mob_id
    assert properties["mobs"][0]["mob_name"] == "Map Mob"
    assert properties["mobs"][0]["mob_url"] == f"/mobs/{mob_id}"


def test_paddock_detail_can_rename_name_and_update_farm_kml(client, app, tmp_path):
    app.instance_path = str(tmp_path)

    with app.app_context():
        farm = Farm(name="Rename Map Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(farm_id=farm.id, name="Old Camp", area_ha=8, grazeable_area_ha=7)
        db.session.add(paddock)
        db.session.commit()
        paddock_id = str(paddock.id)

    _write_google_earth_farm_kml(app, "Rename Map Farm", "Old Camp")

    response = client.post(f"/paddocks/{paddock_id}/rename", data={"name": "New Camp"})
    assert response.status_code == 302

    with app.app_context():
        renamed = Paddock.query.filter_by(id=paddock_id).first()
        assert renamed is not None
        assert renamed.name == "New Camp"

    page = client.get(f"/paddocks/{paddock_id}")
    assert page.status_code == 200
    body = page.data.decode("utf-8")
    assert "Edit Name" in body
    assert "New Camp" in body

    kml_text = (Path(app.instance_path) / "maps" / "Rename Map Farm.kml").read_text(encoding="utf-8")
    assert "gx:CascadingStyle" in kml_text
    assert "<name>New Camp</name>" in kml_text
    assert "<name>Old Camp</name>" not in kml_text


def test_paddock_rename_rejects_blank_and_duplicate_names(client, app, tmp_path):
    app.instance_path = str(tmp_path)

    with app.app_context():
        farm = Farm(name="Rename Validation Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        target = Paddock(farm_id=farm.id, name="Target Camp", area_ha=10, grazeable_area_ha=8)
        existing = Paddock(farm_id=farm.id, name="South Camp", area_ha=9, grazeable_area_ha=7)
        db.session.add_all([target, existing])
        db.session.commit()
        target_id = str(target.id)

    _write_farm_kml(app, "Rename Validation Farm", "Target Camp")

    response = client.post(
        f"/paddocks/{target_id}/rename",
        data={"name": "   "},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Paddock name is required" in response.data.decode("utf-8")

    response = client.post(
        f"/paddocks/{target_id}/rename",
        data={"name": "  south   camp  "},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "A paddock with this name already exists on this farm" in response.data.decode("utf-8")

    with app.app_context():
        unchanged = Paddock.query.filter_by(id=target_id).first()
        assert unchanged is not None
        assert unchanged.name == "Target Camp"

    kml_text = (Path(app.instance_path) / "maps" / "Rename Validation Farm.kml").read_text(encoding="utf-8")
    assert "<name>Target Camp</name>" in kml_text
    assert "<name>South Camp</name>" not in kml_text


def test_mob_balance_edit_reclassifies_and_records_change(client, app):
    with app.app_context():
        farm = Farm(name="Reclass Farm", timezone="SAST")
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
        assert mob_event.tags_csv == (
            "stock,balance edit,reclassification,adjustment out,adjustment in,stock adjustment"
        )
        assert "ram" in mob_event.description
        assert "wether" in mob_event.description
        assert "lamb" in mob_event.description
        assert "young" in mob_event.description


def test_mob_detail_defaults_adjust_stock_to_delta_mode(client, app):
    with app.app_context():
        farm = Farm(name="Adjust Default Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(
            farm_id=farm.id,
            name="Default North Camp",
            area_ha=12,
            grazeable_area_ha=10,
        )
        mob = Mob(farm_id=farm.id, name="Adjust Default Mob", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction="1.0",
            )
        )
        db.session.commit()

        mob_id = str(mob.id)
        paddock_id = str(paddock.id)

    response = client.get(f"/mobs/{mob_id}")
    assert response.status_code == 200

    body = response.data.decode("utf-8")
    assert '<article class="card mob-balances-card">' in body
    assert 'class="table-wrap mob-balances-table-wrap"' in body
    assert '<table id="balanceTable" class="mob-balances-table">' in body
    assert body.index("Current Paddock Allocation") < body.index("Mob Comments / Log Notes")
    assert "<th>Days</th>" in body
    assert f'href="/paddocks/{paddock_id}"' in body
    assert "Default North Camp" in body
    for heading in [
        "Mob Comments / Log Notes",
        "Move Mob",
        "Transfer Stock To Existing Mob",
        "Deactivate Mob",
        "Split Mob",
    ]:
        assert f'<details class="card">\n  <summary><strong>{heading}</strong></summary>' in body
    assert 'option value="adjustment_in" selected' in body
    assert "count (set final total)" in body
    assert "Use count only when you want to set the final head count for this exact line." in body
    assert 'name="allocation_paddock_id"' in body
    assert "All current paddocks" in body
    assert f'<option value="{paddock_id}">Default North Camp</option>' in body


def test_adjust_stock_card_records_log_note_on_mob_detail(client, app):
    with app.app_context():
        farm = Farm(name="Adjust Log Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Adjust Log Mob", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([mob, group])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=5,
            )
        )
        db.session.commit()
        mob_id = str(mob.id)

    response = client.post(
        f"/mobs/{mob_id}/adjust",
        data={
            "species": "Sheep",
            "breed": "Merino",
            "sex": "ewe",
            "age_class": "adult",
            "event_type": "death",
            "quantity": "2",
            "note": "Predator loss confirmed",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    body = response.data.decode("utf-8")
    assert "Mob Comments / Log Notes" in body
    assert "stock, death, stock adjustment" in body
    assert "Stock death recorded for Sheep | Merino | ewe | adult: -2 head." in body
    assert "Balance 5 -&gt; 3 head." in body
    assert "Predator loss confirmed" in body


def test_mob_detail_shows_and_prefills_exact_count_allocations(client, app):
    with app.app_context():
        farm = Farm(name="Mob Detail Count Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(
            farm_id=farm.id,
            name="Exact North Camp",
            area_ha=12,
            grazeable_area_ha=10,
        )
        mob = Mob(farm_id=farm.id, name="Detail Count Mob", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([paddock, mob, group])
        db.session.flush()
        db.session.add(AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=12))
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction="1.0",
        )
        db.session.add(allocation)
        db.session.flush()
        db.session.add(
            GrazingAllocationGroupAssignment(
                grazing_allocation_id=allocation.id,
                animal_group_type_id=group.id,
                head_count=12,
                group_fraction="1.0",
                assigned_lsu="2.0",
            )
        )
        db.session.commit()

        mob_id = str(mob.id)
        paddock_id = str(paddock.id)
        group_id = str(group.id)

    response = client.get(f"/mobs/{mob_id}")
    assert response.status_code == 200

    body = response.data.decode("utf-8")
    assert "Edit Mob Name" not in body
    assert 'data-open-mob-name-modal' in body
    assert 'aria-controls="mob-name-modal"' in body
    assert 'value="Detail Count Mob"' in body
    assert body.index('class="mob-species-totals"') < body.index(
        "Select a balance line, then edit its sex, age class, and head count."
    )
    assert "Its animal type is also loaded into Adjust Stock." in body
    assert "<span>Sheep</span><strong>12</strong> head" in body
    assert "Animal Groups" in body
    assert "Sheep | Merino | ewe | adult:" in body
    assert "12 head" in body
    assert "(exact count)" in body
    assert f'data-selected-value="{paddock_id}"' in body
    assert 'name="count_group_id"' in body
    assert f'value="{group_id}"' in body
    assert 'name="count_head_count"' in body
    assert 'value="12"' in body
    assert 'class="count-group-remaining"' in body
    assert "Calculated:" not in body
    assert "Available:" not in body
    assert "countAllocationTotal" not in body
