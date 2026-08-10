import json
import re
from io import BytesIO
from pathlib import Path

from app.extensions import db
from app.models import (
    Farm,
    NoteAttachment,
    Paddock,
    WaterAsset,
    WaterAssetEvent,
    WaterAssetServedPaddock,
    WaterAssetStateHistory,
    WaterConnection,
)


def _square_ring(lon_start: float, lat_start: float, size_deg: float) -> list[tuple[float, float]]:
    return [
        (lon_start, lat_start),
        (lon_start + size_deg, lat_start),
        (lon_start + size_deg, lat_start + size_deg),
        (lon_start, lat_start + size_deg),
        (lon_start, lat_start),
    ]


def _ring_text(coords: list[tuple[float, float]]) -> str:
    return " ".join(f"{lon:.6f},{lat:.6f},0" for lon, lat in coords)


def _placemark_polygon_xml(name: str, ring: list[tuple[float, float]]) -> str:
    return f"""
    <Placemark>
      <name>{name}</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{_ring_text(ring)}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
"""


def _style_xml(style_id: str, managed_token: str) -> str:
    return f"""
    <Style id="{style_id}">
      <IconStyle>
        <Icon>
          <href>https://example.com/icons/{managed_token}.png</href>
        </Icon>
      </IconStyle>
    </Style>
"""


def _style_map_xml(style_map_id: str, style_id: str) -> str:
    return f"""
    <StyleMap id="{style_map_id}">
      <Pair>
        <key>normal</key>
        <styleUrl>#{style_id}</styleUrl>
      </Pair>
    </StyleMap>
"""


def _point_placemark_xml(
    name: str,
    style_url: str,
    lon: float,
    lat: float,
    *,
    description_html: str = "",
) -> str:
    description_xml = (
        f"\n      <description><![CDATA[{description_html}]]></description>"
        if description_html
        else ""
    )
    return f"""
    <Placemark>
      <name>{name}</name>
      {description_xml}
      <styleUrl>{style_url}</styleUrl>
      <Point>
        <coordinates>{lon:.6f},{lat:.6f},0</coordinates>
      </Point>
    </Placemark>
"""


def _kml_bytes(*bits: str) -> bytes:
    body = "".join(bits)
    return (
        f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    {body}
  </Document>
</kml>
"""
    ).encode("utf-8")


def _post_import_farm(client, *, filename: str, file_bytes: bytes, timezone: str = "SAST", extra_form=None):
    form = {
        "timezone": timezone,
        "farm_kml": (BytesIO(file_bytes), filename),
    }
    if extra_form:
        form.update(extra_form)
    return client.post("/farms/import", data=form)


def _write_map(app, farm_name: str, file_bytes: bytes):
    maps_dir = Path(app.instance_path) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    (maps_dir / f"{farm_name}.kml").write_bytes(file_bytes)


def test_import_farm_creates_water_assets_from_recognized_point_descriptions(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _style_xml("borehole-style", "GENERIC001"),
        _style_xml("trough-style", "GENERIC002"),
        _style_map_xml("trough-style-map", "trough-style"),
        _style_xml("cement-red-style", "0EB62999"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("BH-1", "#borehole-style", 25.0003, -32.0003, description_html="<div>BH</div>"),
        _point_placemark_xml(
            "Trough Red",
            "#trough-style-map",
            25.0007,
            -32.0007,
            description_html="<div>TR</div>",
        ),
        _point_placemark_xml(
            "Cement Dam Red",
            "#cement-red-style",
            25.0015,
            -32.0005,
            description_html="<div>CD</div>",
        ),
    )

    response = _post_import_farm(
        client,
        filename="Water Import Farm.kml",
        file_bytes=file_bytes,
        timezone="Africa/Johannesburg",
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Water Import Farm").first()
        assert farm is not None
        assets = WaterAsset.query.filter_by(farm_id=farm.id).order_by(WaterAsset.name.asc()).all()
        assert [asset.name for asset in assets] == ["BH-1", "Cement Dam Red", "Trough Red"]

        borehole = next(asset for asset in assets if asset.name == "BH-1")
        trough = next(asset for asset in assets if asset.name == "Trough Red")
        cement_dam = next(asset for asset in assets if asset.name == "Cement Dam Red")
        paddock = Paddock.query.filter_by(farm_id=farm.id, name="North Camp").first()

        assert borehole.asset_type == "borehole"
        assert round(float(borehole.latitude), 4) == -32.0003
        assert round(float(borehole.longitude), 4) == 25.0003
        assert borehole.needs_review is True

        assert trough.asset_type == "trough"
        assert trough.location_paddock_id == paddock.id
        assert trough.needs_review is True
        assert trough.status is None
        assert trough.water_level is None
        assert [link.paddock_id for link in trough.served_paddock_links] == [paddock.id]

        assert cement_dam.asset_type == "cement_dam"
        assert cement_dam.status is None
        assert cement_dam.water_level is None
        connections = WaterConnection.query.filter_by(farm_id=farm.id, active=True).all()
        assert len(connections) == 1
        connection = connections[0]
        assert connection.flow_type == "gravity"
        assert connection.source_asset_id == cement_dam.id
        assert connection.destination_asset_id == trough.id
        assert connection.pipe_material == "plastic"
        assert connection.pipe_diameter_spec == "32mm"
        assert connection.pipe_class_spec == "class 2"


def test_import_farm_connects_unassigned_trough_to_nearest_cement_dam_or_tank(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _style_xml("trough-style", "GENERIC202"),
        _style_xml("tank-style", "GENERIC203"),
        _style_xml("cement-style", "GENERIC204"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0015)),
        _point_placemark_xml(
            "Trough A",
            "#trough-style",
            25.0007,
            -32.0007,
            description_html="<div>TR</div>",
        ),
        _point_placemark_xml(
            "Tank A",
            "#tank-style",
            25.0008,
            -32.0008,
            description_html="<div>TK</div>",
        ),
        _point_placemark_xml(
            "Cement Dam A",
            "#cement-style",
            25.0014,
            -32.0014,
            description_html="<div>CD</div>",
        ),
    )

    response = _post_import_farm(
        client,
        filename="Imported Tank Link Farm.kml",
        file_bytes=file_bytes,
        timezone="Africa/Johannesburg",
    )
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Imported Tank Link Farm").first()
        assert farm is not None
        trough = WaterAsset.query.filter_by(farm_id=farm.id, name="Trough A").first()
        tank = WaterAsset.query.filter_by(farm_id=farm.id, name="Tank A").first()
        cement_dam = WaterAsset.query.filter_by(farm_id=farm.id, name="Cement Dam A").first()
        assert trough is not None
        assert tank is not None
        assert cement_dam is not None

        connections = WaterConnection.query.filter_by(farm_id=farm.id, active=True).all()
        assert len(connections) == 1
        connection = connections[0]
        assert connection.flow_type == "gravity"
        assert connection.source_asset_id == tank.id
        assert connection.destination_asset_id == trough.id
        assert connection.pipe_material == "plastic"
        assert connection.pipe_diameter_spec == "32mm"
        assert connection.pipe_class_spec == "class 2"


def test_import_farm_reimports_water_assets_and_archives_missing_imported_rows(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    first_import = _kml_bytes(
        _style_xml("borehole-style", "GENERIC101"),
        _style_xml("trough-style", "GENERIC102"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("BH-1", "#borehole-style", 25.0002, -32.0002, description_html="<div>BH</div>"),
        _point_placemark_xml("TR-1", "#trough-style", 25.0007, -32.0007, description_html="<div>TR</div>"),
    )
    response = _post_import_farm(client, filename="Water Sync Farm.kml", file_bytes=first_import)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Water Sync Farm").first()
        assert farm is not None
        borehole = WaterAsset.query.filter_by(farm_id=farm.id, name="BH-1").first()
        trough = WaterAsset.query.filter_by(farm_id=farm.id, name="TR-1").first()
        manual_asset = WaterAsset(
            farm_id=farm.id,
            name="Manual Tank",
            asset_type="tank",
            active=True,
            needs_review=False,
            latitude=25.005,
            longitude=-32.005,
        )
        db.session.add(manual_asset)
        borehole.status = "limited"
        db.session.commit()
        farm_id = str(farm.id)
        trough_id = str(trough.id)

    second_import = _kml_bytes(
        _style_xml("borehole-style", "GENERIC101"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("BH-1", "#borehole-style", 25.0009, -32.0009, description_html="<div>BH</div>"),
    )
    response = _post_import_farm(client, filename="Water Sync Farm.kml", file_bytes=second_import)
    assert response.status_code == 302

    with app.app_context():
        borehole = WaterAsset.query.filter_by(farm_id=farm_id, name="BH-1").first()
        trough = WaterAsset.query.filter_by(id=trough_id).first()
        manual_asset = WaterAsset.query.filter_by(farm_id=farm_id, name="Manual Tank").first()

        assert round(float(borehole.latitude), 4) == -32.0009
        assert round(float(borehole.longitude), 4) == 25.0009
        assert borehole.status == "limited"
        assert borehole.active is True
        assert borehole.needs_review is True

        assert trough is not None
        assert trough.active is False

        assert manual_asset is not None
        assert manual_asset.active is True
        assert manual_asset.import_placemark_name is None


def test_import_farm_redirects_to_non_imported_water_asset_review(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Manual Review Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        manual_asset = WaterAsset(
            farm_id=farm.id,
            name="Manual Tank",
            asset_type="tank",
            active=True,
        )
        db.session.add(manual_asset)
        db.session.commit()
        farm_id = str(farm.id)

    response = _post_import_farm(
        client,
        filename="Manual Review Farm.kml",
        file_bytes=_kml_bytes(
            _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        ),
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/farms/{farm_id}/import-review/water-assets"

    review_response = client.get(response.headers["Location"])
    assert review_response.status_code == 200
    body = review_response.data.decode("utf-8")
    assert "Review Non-Imported Water Assets" in body
    assert "Manual Tank" in body
    assert "Delete Selected Assets" in body
    assert "Keep All" in body


def test_import_review_can_delete_selected_non_imported_water_assets(client, app):
    with app.app_context():
        farm = Farm(name="Manual Delete Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        manual_tank = WaterAsset(
            farm_id=farm.id,
            name="Manual Tank",
            asset_type="tank",
            active=True,
        )
        imported_tank = WaterAsset(
            farm_id=farm.id,
            name="Imported Tank",
            asset_type="tank",
            active=True,
            import_placemark_name="Imported Tank",
        )
        db.session.add_all([manual_tank, imported_tank])
        db.session.commit()
        farm_id = str(farm.id)
        manual_tank_id = str(manual_tank.id)
        imported_tank_id = str(imported_tank.id)

    response = client.post(
        f"/farms/{farm_id}/import-review/water-assets",
        data={
            "action": "delete",
            "asset_id": [manual_tank_id, imported_tank_id],
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/farms/{farm_id}"

    with app.app_context():
        deleted_asset = WaterAsset.query.filter_by(id=manual_tank_id).first()
        kept_imported_asset = WaterAsset.query.filter_by(id=imported_tank_id).first()
        assert deleted_asset is None
        assert kept_imported_asset is not None


def test_import_farm_renames_same_type_asset_when_reimported_at_same_location(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    first_import = _kml_bytes(
        _style_xml("trough-style", "GENERIC150"),
        _style_xml("cement-style", "GENERIC151"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("Old Trough Name", "#trough-style", 25.0007, -32.0007, description_html="<div>TR</div>"),
        _point_placemark_xml("Dam A", "#cement-style", 25.0009, -32.0005, description_html="<div>CD</div>"),
    )
    response = _post_import_farm(client, filename="Water Rename Farm.kml", file_bytes=first_import)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Water Rename Farm").first()
        assert farm is not None
        original_trough = WaterAsset.query.filter_by(farm_id=farm.id, name="Old Trough Name").first()
        assert original_trough is not None
        farm_id = str(farm.id)
        original_trough_id = str(original_trough.id)

    second_import = _kml_bytes(
        _style_xml("trough-style", "GENERIC150"),
        _style_xml("cement-style", "GENERIC151"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("New Trough Name", "#trough-style", 25.0007, -32.0007, description_html="<div>TR</div>"),
        _point_placemark_xml("Dam A", "#cement-style", 25.0009, -32.0005, description_html="<div>CD</div>"),
    )
    response = _post_import_farm(client, filename="Water Rename Farm.kml", file_bytes=second_import)
    assert response.status_code == 302

    with app.app_context():
        troughs = WaterAsset.query.filter_by(farm_id=farm_id, asset_type="trough").all()
        assert len(troughs) == 1
        trough = troughs[0]
        assert str(trough.id) == original_trough_id
        assert trough.name == "New Trough Name"
        assert trough.import_placemark_name == "New Trough Name"
        assert trough.active is True

        connections = WaterConnection.query.filter_by(farm_id=farm_id, active=True).all()
        assert len(connections) == 1
        assert str(connections[0].destination_asset_id) == original_trough_id


def test_import_farm_repeated_trough_placemark_keeps_single_served_paddock_link(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _style_xml("trough-style", "GENERIC202"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("TR-1", "#trough-style", 25.0004, -32.0004, description_html="<div>TR</div>"),
        _point_placemark_xml("TR-1", "#trough-style", 25.0005, -32.0005, description_html="<div>TR</div>"),
    )

    response = _post_import_farm(client, filename="Repeated Trough Farm.kml", file_bytes=file_bytes)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Repeated Trough Farm").first()
        assert farm is not None
        troughs = WaterAsset.query.filter_by(farm_id=farm.id, name="TR-1").all()
        assert len(troughs) == 1
        trough = troughs[0]
        assert len(trough.served_paddock_links) == 1
        assert trough.asset_type == "trough"


def test_import_farm_applies_weir_defaults_when_missing(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    file_bytes = _kml_bytes(
        _style_xml("generic-style", "GENERIC302"),
        _placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010)),
        _point_placemark_xml("Weir A", "#generic-style", 25.0004, -32.0004, description_html="<div>WR</div>"),
    )

    response = _post_import_farm(client, filename="Weir Default Farm.kml", file_bytes=file_bytes)
    assert response.status_code == 302

    with app.app_context():
        farm = Farm.query.filter_by(name="Weir Default Farm").first()
        assert farm is not None
        paddock = Paddock.query.filter_by(farm_id=farm.id, name="North Camp").first()
        weir = WaterAsset.query.filter_by(farm_id=farm.id, name="Weir A").first()
        assert weir is not None
        assert weir.asset_type == "weir"
        assert weir.status == "operational"
        assert weir.water_level == "empty"
        assert weir.weir_size == "medium"
        assert weir.location_paddock_id == paddock.id
        assert [link.paddock_id for link in weir.served_paddock_links] == [paddock.id]


def test_water_asset_and_connection_api_validation(client, app):
    with app.app_context():
        farm = Farm(name="Water API Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        borehole = WaterAsset(farm_id=farm.id, name="BH-1", asset_type="borehole", active=True)
        windmill = WaterAsset(farm_id=farm.id, name="WM-1", asset_type="windmill", active=True)
        solarpump = WaterAsset(farm_id=farm.id, name="SP-1", asset_type="solarpump", active=True)
        pit = WaterAsset(farm_id=farm.id, name="Pit A", asset_type="pit", active=True)
        cement_dam = WaterAsset(farm_id=farm.id, name="Dam A", asset_type="cement_dam", active=True)
        tank_a = WaterAsset(farm_id=farm.id, name="Tank A", asset_type="tank", active=True)
        tank_b = WaterAsset(farm_id=farm.id, name="Tank B", asset_type="tank", active=True)
        trough = WaterAsset(farm_id=farm.id, name="Trough A", asset_type="trough", active=True)
        db.session.add_all([borehole, windmill, solarpump, pit, cement_dam, tank_a, tank_b, trough])
        db.session.commit()

        farm_id = str(farm.id)
        paddock_id = str(paddock.id)
        borehole_id = str(borehole.id)
        windmill_id = str(windmill.id)
        solarpump_id = str(solarpump.id)
        pit_id = str(pit.id)
        cement_dam_id = str(cement_dam.id)
        tank_a_id = str(tank_a.id)
        tank_b_id = str(tank_b.id)
        trough_id = str(trough.id)

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Tank With Served Paddock",
            "asset_type": "tank",
            "served_paddock_ids": [paddock_id],
        },
    )
    assert response.status_code == 400
    assert "Only ground_dam, weir, and trough assets can serve paddocks" in response.get_json()["error"]

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "North Weir",
            "asset_type": "weir",
            "served_paddock_ids": [paddock_id],
        },
    )
    assert response.status_code == 201
    weir_payload = response.get_json()
    assert weir_payload["status"] == "operational"
    assert weir_payload["water_level"] == "empty"
    assert weir_payload["weir_size"] == "medium"

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Bad Trough",
            "asset_type": "trough",
            "status": "dry",
        },
    )
    assert response.status_code == 400
    assert "status must be one of" in response.get_json()["error"]

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Bad Tank Material",
            "asset_type": "tank",
            "material": "earth",
        },
    )
    assert response.status_code == 400
    assert "material is not valid for asset_type tank" in response.get_json()["error"]

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Bad Windmill Capacity",
            "asset_type": "windmill",
            "capacity_m3": 10,
        },
    )
    assert response.status_code == 400
    assert "capacity_m3 is not valid for asset_type windmill" in response.get_json()["error"]

    response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Bad Tank Source System",
            "asset_type": "tank",
            "source_system": "borehole",
        },
    )
    assert response.status_code == 400
    assert "source_system is only valid for solarpump assets" in response.get_json()["error"]

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "gravity",
            "source_asset_id": borehole_id,
            "destination_asset_id": trough_id,
            "active": True,
        },
    )
    assert response.status_code == 400
    assert "gravity only allows" in response.get_json()["error"]

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "gravity",
            "source_asset_id": cement_dam_id,
            "destination_asset_id": trough_id,
            "active": True,
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "gravity",
            "source_asset_id": tank_a_id,
            "destination_asset_id": pit_id,
            "active": True,
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "pumped",
            "source_asset_id": borehole_id,
            "destination_asset_id": tank_a_id,
            "pump_asset_id": windmill_id,
            "active": True,
        },
    )
    assert response.status_code == 201
    connection_id = response.get_json()["id"]

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "pumped",
            "source_asset_id": tank_a_id,
            "destination_asset_id": pit_id,
            "pump_asset_id": solarpump_id,
            "active": True,
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "pumped",
            "source_asset_id": borehole_id,
            "destination_asset_id": tank_b_id,
            "pump_asset_id": windmill_id,
            "active": True,
        },
    )
    assert response.status_code == 201
    second_connection_id = response.get_json()["id"]

    get_response = client.get(f"/api/water-connections/{connection_id}")
    assert get_response.status_code == 200
    payload = get_response.get_json()
    assert payload["flow_type"] == "pumped"
    assert payload["pump_asset_id"] == windmill_id

    second_get_response = client.get(f"/api/water-connections/{second_connection_id}")
    assert second_get_response.status_code == 200
    second_payload = second_get_response.get_json()
    assert second_payload["flow_type"] == "pumped"
    assert second_payload["pump_asset_id"] == windmill_id


def test_update_water_asset_type_change_clears_hidden_solar_fields(client, app):
    with app.app_context():
        farm = Farm(name="Solar Edit Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Solar Pump A",
            asset_type="solarpump",
            active=True,
            status="operational",
            solar_brand="SunPower",
            solar_kw=5.5,
            solar_head_m=42,
            source_system="borehole",
        )
        db.session.add(asset)
        db.session.commit()
        asset_id = str(asset.id)

    response = client.patch(
        f"/api/water-assets/{asset_id}",
        json={
            "name": "Tank A",
            "asset_type": "tank",
            "active": True,
            "status": "operational",
            "water_level": "half",
            "capacity_m3": 15,
            "material": "steel",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["asset_type"] == "tank"
    assert payload["solar_brand"] is None
    assert payload["solar_kw"] is None
    assert payload["solar_head_m"] is None
    assert payload["source_system"] is None
    assert payload["water_level"] == "half"
    assert payload["capacity_m3"] == 15.0
    assert payload["material"] == "steel"


def test_water_asset_state_history_records_create_and_future_state_updates(client, app):
    with app.app_context():
        farm = Farm(name="Water History Farm", timezone="SAST")
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    create_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "History Tank",
            "asset_type": "tank",
            "active": True,
            "status": "operational",
            "water_level": "low",
        },
    )
    assert create_response.status_code == 201
    asset_id = create_response.get_json()["id"]

    with app.app_context():
        rows = WaterAssetStateHistory.query.filter_by(water_asset_id=asset_id).all()
        assert len(rows) == 1
        assert rows[0].change_type == "created"
        assert rows[0].previous_status is None
        assert rows[0].status == "operational"
        assert rows[0].water_level == "low"

    update_response = client.patch(
        f"/api/water-assets/{asset_id}",
        json={"water_level": "full"},
    )
    assert update_response.status_code == 200

    history_response = client.get(f"/api/water-assets/{asset_id}/state-history")
    assert history_response.status_code == 200
    history_payload = history_response.get_json()
    assert len(history_payload) == 2
    updated = next(row for row in history_payload if row["change_type"] == "updated")
    assert updated["previous_active"] is True
    assert updated["previous_status"] == "operational"
    assert updated["previous_water_level"] == "low"
    assert updated["active"] is True
    assert updated["status"] == "operational"
    assert updated["water_level"] == "full"

    rename_response = client.patch(
        f"/api/water-assets/{asset_id}",
        json={"name": "History Tank Renamed"},
    )
    assert rename_response.status_code == 200
    with app.app_context():
        assert WaterAssetStateHistory.query.filter_by(water_asset_id=asset_id).count() == 2


def test_update_water_asset_type_change_clears_hidden_windmill_fields(client, app):
    with app.app_context():
        farm = Farm(name="Windmill Edit Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Windmill A",
            asset_type="windmill",
            active=True,
            status="operational",
            windmill_size_ft=12,
        )
        db.session.add(asset)
        db.session.commit()
        asset_id = str(asset.id)

    response = client.patch(
        f"/api/water-assets/{asset_id}",
        json={
            "name": "Trough A",
            "asset_type": "trough",
            "active": True,
            "status": "operational",
            "water_level": "half",
            "trough_size": "medium",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["asset_type"] == "trough"
    assert payload["windmill_size_ft"] is None
    assert payload["water_level"] == "half"
    assert payload["trough_size"] == "medium"


def test_weir_defaults_apply_when_blank_on_update(client, app):
    with app.app_context():
        farm = Farm(name="Weir Default Update Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Weir A",
            asset_type="weir",
            active=True,
            status="damaged",
            water_level="high",
            weir_size="large",
        )
        db.session.add(asset)
        db.session.commit()
        asset_id = str(asset.id)

    response = client.patch(
        f"/api/water-assets/{asset_id}",
        json={
            "name": "Weir A",
            "asset_type": "weir",
            "active": True,
            "status": "",
            "water_level": "",
            "weir_size": "",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "operational"
    assert payload["water_level"] == "empty"
    assert payload["weir_size"] == "medium"


def test_location_bound_served_paddocks_follow_ground_dam_and_weir_location(client, app):
    with app.app_context():
        farm = Farm(name="Location Bound Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Camp", area_ha=9, grazeable_area_ha=9)
        db.session.add_all([north, south])
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)
        south_id = str(south.id)

    ground_dam_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Ground Dam A",
            "asset_type": "ground_dam",
            "active": True,
            "location_paddock_id": north_id,
            "served_paddock_ids": [south_id],
        },
    )
    assert ground_dam_response.status_code == 201
    ground_dam_payload = ground_dam_response.get_json()
    assert ground_dam_payload["served_paddock_ids"] == [north_id]

    weir_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Weir A",
            "asset_type": "weir",
            "active": True,
            "location_paddock_id": south_id,
            "served_paddock_ids": [north_id],
        },
    )
    assert weir_response.status_code == 201
    weir_payload = weir_response.get_json()
    assert weir_payload["served_paddock_ids"] == [south_id]

    updated_weir_response = client.patch(
        f"/api/water-assets/{weir_payload['id']}",
        json={
            "name": "Weir A",
            "asset_type": "weir",
            "active": True,
            "location_paddock_id": north_id,
        },
    )
    assert updated_weir_response.status_code == 200
    assert updated_weir_response.get_json()["served_paddock_ids"] == [north_id]


def test_water_assets_auto_connect_troughs_to_nearest_cement_dam(client, app):
    with app.app_context():
        farm = Farm(name="Default Trough Link Farm", timezone="SAST")
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    trough_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Trough A",
            "asset_type": "trough",
            "active": True,
            "latitude": -32.0008,
            "longitude": 25.0008,
        },
    )
    assert trough_response.status_code == 201
    trough_id = trough_response.get_json()["id"]

    far_dam_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Far Dam",
            "asset_type": "cement_dam",
            "active": True,
            "latitude": -32.0030,
            "longitude": 25.0030,
        },
    )
    assert far_dam_response.status_code == 201

    connection_payload = client.get(f"/api/water-connections?farm_id={farm_id}").get_json()
    assert len(connection_payload) == 1
    assert connection_payload[0]["destination_asset_id"] == trough_id
    assert connection_payload[0]["source_asset_name"] == "Far Dam"
    assert connection_payload[0]["pipe_material"] == "plastic"
    assert connection_payload[0]["pipe_diameter_spec"] == "32mm"
    assert connection_payload[0]["pipe_class_spec"] == "class 2"

    near_dam_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Near Dam",
            "asset_type": "cement_dam",
            "active": True,
            "latitude": -32.0009,
            "longitude": 25.0009,
        },
    )
    assert near_dam_response.status_code == 201

    connection_payload = client.get(f"/api/water-connections?farm_id={farm_id}").get_json()
    assert len(connection_payload) == 1
    assert connection_payload[0]["source_asset_name"] == "Far Dam"
    assert connection_payload[0]["destination_asset_id"] == trough_id


def test_water_assets_do_not_auto_connect_troughs_with_existing_manual_connections(client, app):
    with app.app_context():
        farm = Farm(name="Manual Trough Link Farm", timezone="SAST")
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    trough_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Trough A",
            "asset_type": "trough",
            "active": True,
            "latitude": -32.0008,
            "longitude": 25.0008,
        },
    )
    assert trough_response.status_code == 201
    trough_id = trough_response.get_json()["id"]

    ground_dam_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Ground Dam A",
            "asset_type": "ground_dam",
            "active": True,
            "latitude": -32.0012,
            "longitude": 25.0012,
        },
    )
    assert ground_dam_response.status_code == 201
    ground_dam_id = ground_dam_response.get_json()["id"]

    manual_connection_response = client.post(
        "/api/water-connections",
        json={
            "farm_id": farm_id,
            "flow_type": "gravity",
            "source_asset_id": ground_dam_id,
            "destination_asset_id": trough_id,
            "active": False,
            "pipe_material": "steel",
        },
    )
    assert manual_connection_response.status_code == 201

    cement_dam_response = client.post(
        "/api/water-assets",
        json={
            "farm_id": farm_id,
            "name": "Cement Dam A",
            "asset_type": "cement_dam",
            "active": True,
            "latitude": -32.0009,
            "longitude": 25.0009,
        },
    )
    assert cement_dam_response.status_code == 201

    connection_payload = client.get(f"/api/water-connections?farm_id={farm_id}").get_json()
    assert len(connection_payload) == 1
    assert connection_payload[0]["source_asset_id"] == ground_dam_id
    assert connection_payload[0]["destination_asset_id"] == trough_id
    assert connection_payload[0]["active"] is False
    assert connection_payload[0]["pipe_material"] == "steel"


def test_farm_map_data_propagates_empty_dam_water_levels_to_connected_troughs(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Empty Dam Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        cement_dam = WaterAsset(
            farm_id=farm.id,
            name="Dam A",
            asset_type="cement_dam",
            active=True,
            water_level="empty",
            latitude=-32.0002,
            longitude=25.0002,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
            water_level="full",
            latitude=-32.0006,
            longitude=25.0006,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([cement_dam, trough])
        db.session.flush()
        db.session.add(
            WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id)
        )
        db.session.add(
            WaterConnection(
                farm_id=farm.id,
                active=True,
                flow_type="gravity",
                source_asset_id=cement_dam.id,
                destination_asset_id=trough.id,
            )
        )
        db.session.commit()
        farm_id = str(farm.id)
        trough_id = str(trough.id)

    _write_map(
        app,
        "Empty Dam Water Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    trough_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "water_asset"
        and feature["properties"]["name"] == "Trough A"
    )
    assert trough_feature["properties"]["water_level"] == "empty"
    assert "Dam A" in trough_feature["properties"]["network_warning"]
    asset_response = client.get(f"/api/water-assets/{trough_id}")
    assert asset_response.status_code == 200
    assert asset_response.get_json()["water_level"] == "empty"


def test_operational_trough_mirrors_connected_source_water_level(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Mirrored Trough Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        cement_dam = WaterAsset(
            farm_id=farm.id,
            name="Dam A",
            asset_type="cement_dam",
            active=True,
            water_level="high",
            latitude=-32.0002,
            longitude=25.0002,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="empty",
            latitude=-32.0006,
            longitude=25.0006,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([cement_dam, trough])
        db.session.flush()
        db.session.add(
            WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id)
        )
        db.session.add(
            WaterConnection(
                farm_id=farm.id,
                active=True,
                flow_type="gravity",
                source_asset_id=cement_dam.id,
                destination_asset_id=trough.id,
            )
        )
        db.session.commit()
        farm_id = str(farm.id)
        trough_id = str(trough.id)

    asset_response = client.get(f"/api/water-assets/{trough_id}")
    assert asset_response.status_code == 200
    asset_payload = asset_response.get_json()
    assert asset_payload["water_level"] == "high"
    assert asset_payload["reported_water_level"] == "empty"

    _write_map(
        app,
        "Mirrored Trough Water Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    trough_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "water_asset"
        and feature["properties"]["name"] == "Trough A"
    )
    assert trough_feature["properties"]["water_level"] == "high"
    assert trough_feature["properties"]["reported_water_level"] == "empty"


def test_farm_map_data_returns_water_features_with_and_without_kml(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Mapped Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        source = WaterAsset(
            farm_id=farm.id,
            name="BH-1",
            asset_type="borehole",
            active=True,
            latitude=-32.0002,
            longitude=25.0002,
        )
        pump = WaterAsset(
            farm_id=farm.id,
            name="WM-1",
            asset_type="windmill",
            active=True,
            latitude=-32.0004,
            longitude=25.0004,
        )
        tank = WaterAsset(
            farm_id=farm.id,
            name="Tank A",
            asset_type="tank",
            active=True,
            latitude=-32.0007,
            longitude=25.0007,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([source, pump, tank])
        db.session.flush()
        db.session.add(
            WaterConnection(
                farm_id=farm.id,
                active=True,
                flow_type="pumped",
                source_asset_id=source.id,
                destination_asset_id=tank.id,
                pump_asset_id=pump.id,
            )
        )
        db.session.commit()
        farm_id = str(farm.id)

    _write_map(
        app,
        "Mapped Water Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    feature_types = {feature["properties"]["feature_type"] for feature in payload["features"]}
    assert "paddock" in feature_types
    assert "water_asset" in feature_types
    assert "water_connection" in feature_types
    assert payload["warnings"] == []
    water_asset_feature = next(
        feature for feature in payload["features"] if feature["properties"]["feature_type"] == "water_asset"
    )
    water_connection_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "water_connection"
    )
    assert water_asset_feature["properties"]["asset_workspace_url"] == f"/farms/{farm_id}/water"
    assert water_connection_feature["properties"]["pump_asset_type"] == "windmill"

    dashboard_payload = client.get("/dashboard/map-data").get_json()
    dashboard_feature_types = {feature["properties"]["feature_type"] for feature in dashboard_payload["features"]}
    assert "water_asset" not in dashboard_feature_types
    assert "water_connection" not in dashboard_feature_types

    with app.app_context():
        farm = Farm(name="No Kml Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        trough = WaterAsset(
            farm_id=farm.id,
            name="TR-1",
            asset_type="trough",
            active=True,
            latitude=-32.5000,
            longitude=25.5000,
        )
        db.session.add(trough)
        db.session.commit()
        missing_farm_id = str(farm.id)

    response = client.get(f"/farms/{missing_farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    assert any(feature["properties"]["feature_type"] == "water_asset" for feature in payload["features"])
    assert payload["warnings"]
    assert "Farm map KML file not found" in payload["warnings"][0]


def test_farm_map_data_marks_paddocks_with_critical_water_alerts(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Pump Alert Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        borehole = WaterAsset(
            farm_id=farm.id,
            name="BH-1",
            asset_type="borehole",
            active=True,
            latitude=-32.0002,
            longitude=25.0002,
        )
        windmill = WaterAsset(
            farm_id=farm.id,
            name="WM-1",
            asset_type="windmill",
            active=True,
            status="down",
            latitude=-32.0004,
            longitude=25.0004,
        )
        cement_dam = WaterAsset(
            farm_id=farm.id,
            name="Dam A",
            asset_type="cement_dam",
            active=True,
            water_level="half",
            latitude=-32.0006,
            longitude=25.0006,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
            water_level="half",
            latitude=-32.0008,
            longitude=25.0008,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([borehole, windmill, cement_dam, trough])
        db.session.flush()
        db.session.add(
            WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id)
        )
        db.session.add_all(
            [
                WaterConnection(
                    farm_id=farm.id,
                    active=True,
                    flow_type="pumped",
                    source_asset_id=borehole.id,
                    destination_asset_id=cement_dam.id,
                    pump_asset_id=windmill.id,
                ),
                WaterConnection(
                    farm_id=farm.id,
                    active=True,
                    flow_type="gravity",
                    source_asset_id=cement_dam.id,
                    destination_asset_id=trough.id,
                ),
            ]
        )
        db.session.commit()
        farm_id = str(farm.id)
        paddock_id = str(paddock.id)

    _write_map(
        app,
        "Pump Alert Water Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    paddock_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "paddock"
        and feature["properties"]["paddock_id"] == paddock_id
    )
    assert paddock_feature["properties"]["water_alert_level"] == "critical"
    assert "No working pumped supply path reaches this paddock." in (
        paddock_feature["properties"]["water_alert_message"]
    )
    assert "WM-1" in paddock_feature["properties"]["water_alert_message"]


def test_farm_map_data_does_not_mark_paddock_empty_when_served_weir_has_water(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Served Weir Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="empty",
            latitude=-32.0008,
            longitude=25.0008,
            location_paddock_id=paddock.id,
        )
        weir = WaterAsset(
            farm_id=farm.id,
            name="Weir A",
            asset_type="weir",
            active=True,
            status="operational",
            water_level="high",
            latitude=-32.0012,
            longitude=25.0012,
            location_paddock_id=paddock.id,
        )
        db.session.add_all([trough, weir])
        db.session.flush()
        db.session.add_all(
            [
                WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id),
                WaterAssetServedPaddock(water_asset_id=weir.id, paddock_id=paddock.id),
            ]
        )
        db.session.commit()
        farm_id = str(farm.id)
        paddock_id = str(paddock.id)

    _write_map(
        app,
        "Served Weir Water Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    paddock_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "paddock"
        and feature["properties"]["paddock_id"] == paddock_id
    )
    assert paddock_feature["properties"]["water_alert_level"] is None
    assert paddock_feature["properties"]["water_alert_message"] is None


def test_farm_map_data_counts_served_ground_dams_and_weirs_for_paddock_water(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Mixed Water Point Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        borehole = WaterAsset(
            farm_id=farm.id,
            name="BH-1",
            asset_type="borehole",
            active=True,
            latitude=-32.0002,
            longitude=25.0002,
        )
        windmill = WaterAsset(
            farm_id=farm.id,
            name="WM-1",
            asset_type="windmill",
            active=True,
            status="down",
            latitude=-32.0004,
            longitude=25.0004,
        )
        cement_dam = WaterAsset(
            farm_id=farm.id,
            name="Dam A",
            asset_type="cement_dam",
            active=True,
            water_level="half",
            latitude=-32.0006,
            longitude=25.0006,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
            water_level="half",
            latitude=-32.0008,
            longitude=25.0008,
            location_paddock_id=paddock.id,
        )
        ground_dam = WaterAsset(
            farm_id=farm.id,
            name="Ground Dam A",
            asset_type="ground_dam",
            active=True,
            water_level="high",
            latitude=-32.0010,
            longitude=25.0010,
        )
        weir = WaterAsset(
            farm_id=farm.id,
            name="Weir A",
            asset_type="weir",
            active=True,
            water_level="high",
            latitude=-32.0012,
            longitude=25.0012,
        )
        db.session.add_all([borehole, windmill, cement_dam, trough, ground_dam, weir])
        db.session.flush()
        db.session.add_all(
            [
                WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=paddock.id),
                WaterAssetServedPaddock(water_asset_id=ground_dam.id, paddock_id=paddock.id),
                WaterAssetServedPaddock(water_asset_id=weir.id, paddock_id=paddock.id),
            ]
        )
        db.session.add_all(
            [
                WaterConnection(
                    farm_id=farm.id,
                    active=True,
                    flow_type="pumped",
                    source_asset_id=borehole.id,
                    destination_asset_id=cement_dam.id,
                    pump_asset_id=windmill.id,
                ),
                WaterConnection(
                    farm_id=farm.id,
                    active=True,
                    flow_type="gravity",
                    source_asset_id=cement_dam.id,
                    destination_asset_id=trough.id,
                ),
            ]
        )
        db.session.commit()
        farm_id = str(farm.id)
        paddock_id = str(paddock.id)

    _write_map(
        app,
        "Mixed Water Point Farm",
        _kml_bytes(_placemark_polygon_xml("North Camp", _square_ring(25.0000, -32.0000, 0.0010))),
    )

    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    payload = response.get_json()
    paddock_feature = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["feature_type"] == "paddock"
        and feature["properties"]["paddock_id"] == paddock_id
    )
    assert paddock_feature["properties"]["water_alert_level"] is None
    assert paddock_feature["properties"]["water_alert_message"] is None


def test_farm_water_workspace_renders_map_filters_and_satellite_mode(client, app):
    with app.app_context():
        farm = Farm(name="Filtered Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(
        f"/farms/{farm_id}/water?map_filters_applied=1&asset_type=tank&asset_type=trough"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'data-base-layer="satellite"' in body
    assert 'data-paddock-fill="outline"' in body
    assert 'data-show-stock-floats="0"' in body
    assert 'data-water-asset-filter="tank,trough"' in body
    assert 'value="tank"' in body
    assert 'value="trough"' in body
    assert "Apply Map Filters" in body
    assert "Show All Types" in body


def test_farm_water_workspace_collapses_create_forms_and_filters_water_assets(client, app):
    with app.app_context():
        farm = Farm(name="Water Asset Filter Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Camp", area_ha=8, grazeable_area_ha=8)
        db.session.add_all([north, south])
        db.session.flush()
        tank = WaterAsset(
            farm_id=farm.id,
            name="Tank Alpha",
            asset_type="tank",
            active=True,
            location_paddock_id=north.id,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="Trough Beta",
            asset_type="trough",
            active=True,
            location_paddock_id=south.id,
        )
        weir = WaterAsset(
            farm_id=farm.id,
            name="Weir Review",
            asset_type="weir",
            active=True,
            needs_review=True,
        )
        db.session.add_all([tank, trough, weir])
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)

    response = client.get(
        f"/farms/{farm_id}/water?"
        f"water_asset_filters_applied=1&water_asset_type=tank&"
        f"water_asset_state=active&water_asset_paddock_id={north_id}"
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    water_assets_section = body.split('<section class="card" id="water-assets">', 1)[1].split(
        "<dialog",
        1,
    )[0]

    assert 'id="add-water-asset-panel"' in body
    assert 'id="add-water-connection-panel"' in body
    assert '<span class="btn">Add Water Asset</span>' in body
    assert '<span class="btn">Add Water Connection</span>' in body
    assert "Apply Water Asset Filters" in water_assets_section
    assert "1 of 3 shown" in water_assets_section
    assert "Tank Alpha" in water_assets_section
    assert "Trough Beta" not in water_assets_section
    assert "Weir Review" not in water_assets_section


def test_farm_water_workspace_renders_asset_modal_launchers(client, app):
    with app.app_context():
        farm = Farm(name="Modal Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Tank Alpha",
            asset_type="tank",
            active=True,
            location_paddock_id=paddock.id,
        )
        db.session.add(asset)
        db.session.commit()
        farm_id = str(farm.id)
        asset_id = str(asset.id)

    response = client.get(f"/farms/{farm_id}/water")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'id="water-asset-modal"' in body
    assert f'data-open-water-asset-modal="{asset_id}"' in body
    assert 'data-asset-api-base="/api/water-assets"' in body
    assert 'data-create-connection-url="/farms/' in body
    assert 'data-water-asset-connections' in body
    assert 'farm_water_connections.js' in body
    assert 'id="water-asset-editor-config"' in body
    config_match = re.search(
        r'<script id="water-asset-editor-config" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )
    assert config_match is not None
    editor_config = json.loads(config_match.group(1))
    assert editor_config["windmillSizeOptions"] == [10, 12, 14]
    assert editor_config["weirSizeOptions"] == ["small", "medium", "large"]
    assert editor_config["solarFieldTypes"] == ["solarpump"]
    assert editor_config["sourceSystemTypes"] == ["solarpump"]
    assert editor_config["weirFieldTypes"] == ["weir"]
    assert editor_config["troughFieldTypes"] == ["trough"]
    assert editor_config["servedPaddockTypes"] == ["ground_dam", "trough", "weir"]
    assert editor_config["locationBoundServedPaddockTypes"] == ["ground_dam", "weir"]
    assert "windmill" not in editor_config["waterLevelTypes"]
    assert "solarpump" not in editor_config["waterLevelTypes"]
    assert "windmill" not in editor_config["capacityTypes"]
    assert "solarpump" not in editor_config["capacityTypes"]
    assert editor_config["defaultTroughConnection"]["pipeMaterial"] == "plastic"
    assert any(
        connection["source_asset_id"] == asset_id or connection["destination_asset_id"] == asset_id
        for connection in editor_config["connectionRecords"]
    ) is False


def test_farm_water_workspace_records_water_asset_note_with_image(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Water Note Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Photo Trough",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="full",
        )
        db.session.add(asset)
        db.session.flush()
        farm_id = str(farm.id)
        asset_id = str(asset.id)
        db.session.commit()

    response = client.post(
        f"/farms/{farm_id}/water/assets/{asset_id}/events",
        data={
            "event_tags": "leak, inspection",
            "event_description": "Trough inspected with field photo",
            "event_images": (BytesIO(b"water-note-photo"), "trough.jpg", "image/jpeg"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302

    with app.app_context():
        event = WaterAssetEvent.query.filter_by(water_asset_id=asset_id).one()
        attachment = NoteAttachment.query.filter_by(water_asset_event_id=event.id).one()
        assert event.description == "Trough inspected with field photo"
        assert attachment.original_filename == "trough.jpg"
        attachment_id = str(attachment.id)

    page = client.get(f"/farms/{farm_id}/water")
    body = page.data.decode("utf-8")
    assert "Trough inspected with field photo" in body
    assert f"/note-attachments/{attachment_id}" in body

    image_response = client.get(f"/note-attachments/{attachment_id}")
    assert image_response.status_code == 200
    assert image_response.data == b"water-note-photo"


def test_update_water_asset_form_reopens_asset_modal_on_validation_error(client, app):
    with app.app_context():
        farm = Farm(name="Water Redirect Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        asset = WaterAsset(
            farm_id=farm.id,
            name="Trough A",
            asset_type="trough",
            active=True,
        )
        db.session.add(asset)
        db.session.commit()
        farm_id = str(farm.id)
        asset_id = str(asset.id)

    response = client.post(
        f"/farms/{farm_id}/water/assets/{asset_id}",
        data={
            "name": "Trough A",
            "asset_type": "trough",
            "status": "dry",
            "active": "1",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/farms/{farm_id}/water?open_asset_id={asset_id}"


def test_create_water_connection_form_accepts_locked_flow_type_fallback(client, app):
    with app.app_context():
        farm = Farm(name="Water Connection Form Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        source_asset = WaterAsset(
            farm_id=farm.id,
            name="Dam A",
            asset_type="cement_dam",
            active=True,
        )
        destination_asset = WaterAsset(
            farm_id=farm.id,
            name="Dam B",
            asset_type="cement_dam",
            active=True,
        )
        pump_asset = WaterAsset(
            farm_id=farm.id,
            name="Windmill A",
            asset_type="windmill",
            active=True,
        )
        db.session.add_all([source_asset, destination_asset, pump_asset])
        db.session.commit()
        farm_id = str(farm.id)
        source_asset_id = str(source_asset.id)
        destination_asset_id = str(destination_asset.id)
        pump_asset_id = str(pump_asset.id)

    response = client.post(
        f"/farms/{farm_id}/water/connections",
        data={
            "active": "1",
            "_flow_type": "pumped",
            "source_asset_id": source_asset_id,
            "destination_asset_id": destination_asset_id,
            "pump_asset_id": pump_asset_id,
            "open_asset_id": pump_asset_id,
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/farms/{farm_id}/water?open_asset_id={pump_asset_id}"

    with app.app_context():
        connection = WaterConnection.query.filter_by(
            farm_id=farm_id,
            source_asset_id=source_asset_id,
            destination_asset_id=destination_asset_id,
            pump_asset_id=pump_asset_id,
        ).first()
        assert connection is not None
        assert connection.flow_type == "pumped"


def test_farm_water_mass_update_renders_weir_table(client, app):
    with app.app_context():
        farm = Farm(name="Weir Mass Update Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        first_weir = WaterAsset(farm_id=farm.id, name="Weir Alpha", asset_type="weir", active=True)
        second_weir = WaterAsset(farm_id=farm.id, name="Weir Beta", asset_type="weir", active=True)
        tank = WaterAsset(farm_id=farm.id, name="Tank Alpha", asset_type="tank", active=True)
        db.session.add_all([first_weir, second_weir, tank])
        db.session.commit()
        farm_id = str(farm.id)
        first_weir_id = str(first_weir.id)
        second_weir_id = str(second_weir.id)

    response = client.get(f"/farms/{farm_id}/water/mass-update?asset_type=weir")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Step 2. Update Assets" in body
    assert "Weir Alpha" in body
    assert "Weir Beta" in body
    assert "Tank Alpha" not in body
    assert "Weir Size" in body
    assert 'data-water-mass-update-fullscreen-toggle' in body
    assert 'farm_water_mass_update.js' in body
    assert f'name="weir_size__{first_weir_id}"' in body
    assert f'name="weir_size__{second_weir_id}"' in body


def test_farm_water_mass_update_updates_weirs(client, app):
    with app.app_context():
        farm = Farm(name="Weir Mass Save Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Camp", area_ha=9, grazeable_area_ha=9)
        db.session.add_all([north, south])
        db.session.flush()
        first_weir = WaterAsset(
            farm_id=farm.id,
            name="Weir Alpha",
            asset_type="weir",
            active=True,
            status="operational",
            water_level="empty",
            weir_size="medium",
            location_paddock_id=north.id,
        )
        second_weir = WaterAsset(
            farm_id=farm.id,
            name="Weir Beta",
            asset_type="weir",
            active=True,
            status="silted",
            water_level="low",
            weir_size="small",
            location_paddock_id=south.id,
        )
        db.session.add_all([first_weir, second_weir])
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)
        south_id = str(south.id)
        first_weir_id = str(first_weir.id)
        second_weir_id = str(second_weir.id)

    response = client.post(
        f"/farms/{farm_id}/water/mass-update",
        data={
            "asset_type": "weir",
            "asset_id": [first_weir_id, second_weir_id],
            f"name__{first_weir_id}": "Weir Alpha Updated",
            f"active__{first_weir_id}": ["0", "1"],
            f"needs_review__{first_weir_id}": ["0", "1"],
            f"location_paddock_id__{first_weir_id}": south_id,
            f"latitude__{first_weir_id}": "-32.1000",
            f"longitude__{first_weir_id}": "25.1000",
            f"altitude_m__{first_weir_id}": "123.45",
            f"status__{first_weir_id}": "",
            f"water_level__{first_weir_id}": "",
            f"weir_size__{first_weir_id}": "",
            f"name__{second_weir_id}": "Weir Beta Updated",
            f"active__{second_weir_id}": "0",
            f"needs_review__{second_weir_id}": "0",
            f"location_paddock_id__{second_weir_id}": north_id,
            f"latitude__{second_weir_id}": "-32.2000",
            f"longitude__{second_weir_id}": "25.2000",
            f"altitude_m__{second_weir_id}": "140.00",
            f"status__{second_weir_id}": "damaged",
            f"water_level__{second_weir_id}": "half",
            f"weir_size__{second_weir_id}": "large",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/farms/{farm_id}/water/mass-update?asset_type=weir"

    with app.app_context():
        first_weir = WaterAsset.query.get(first_weir_id)
        second_weir = WaterAsset.query.get(second_weir_id)
        assert first_weir is not None
        assert first_weir.name == "Weir Alpha Updated"
        assert first_weir.needs_review is True
        assert first_weir.location_paddock_id == south_id
        assert round(float(first_weir.latitude), 4) == -32.1
        assert round(float(first_weir.longitude), 4) == 25.1
        assert round(float(first_weir.altitude_m), 2) == 123.45
        assert first_weir.status == "operational"
        assert first_weir.water_level == "empty"
        assert first_weir.weir_size == "medium"
        assert [link.paddock_id for link in first_weir.served_paddock_links] == [south_id]

        assert second_weir is not None
        assert second_weir.name == "Weir Beta Updated"
        assert second_weir.active is False
        assert second_weir.needs_review is False
        assert second_weir.location_paddock_id == north_id
        assert round(float(second_weir.latitude), 4) == -32.2
        assert round(float(second_weir.longitude), 4) == 25.2
        assert round(float(second_weir.altitude_m), 2) == 140.0
        assert second_weir.status == "damaged"
        assert second_weir.water_level == "half"
        assert second_weir.weir_size == "large"
        assert [link.paddock_id for link in second_weir.served_paddock_links] == [north_id]


def test_paddock_detail_shows_local_assets_and_serving_water_points(client, app):
    with app.app_context():
        farm = Farm(name="Paddock Water Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()
        north = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Camp", area_ha=9, grazeable_area_ha=9)
        db.session.add_all([north, south])
        db.session.flush()

        tank = WaterAsset(
            farm_id=farm.id,
            name="North Tank",
            asset_type="tank",
            active=True,
            location_paddock_id=north.id,
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="South Trough",
            asset_type="trough",
            active=True,
            location_paddock_id=south.id,
        )
        weir = WaterAsset(
            farm_id=farm.id,
            name="Boundary Weir",
            asset_type="weir",
            active=True,
        )
        db.session.add_all([tank, trough, weir])
        db.session.flush()
        db.session.add(WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=north.id))
        db.session.add(WaterAssetServedPaddock(water_asset_id=weir.id, paddock_id=north.id))
        db.session.commit()
        paddock_id = str(north.id)

    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Water For This Paddock" in body
    assert "North Tank" in body
    assert "Water Points Serving This Paddock" in body
    assert "Boundary Weir" in body
    assert "South Trough" in body
    assert "Open Farm Water Workspace" in body
