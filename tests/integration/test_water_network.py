from io import BytesIO
from pathlib import Path

from app.extensions import db
from app.models import Farm, Paddock, WaterAsset, WaterAssetServedPaddock, WaterConnection


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


def _post_import_farm(client, *, filename: str, file_bytes: bytes, timezone: str = "UTC", extra_form=None):
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


def test_water_asset_and_connection_api_validation(client, app):
    with app.app_context():
        farm = Farm(name="Water API Farm", timezone="UTC")
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=12, grazeable_area_ha=12)
        db.session.add(paddock)
        db.session.flush()

        borehole = WaterAsset(farm_id=farm.id, name="BH-1", asset_type="borehole", active=True)
        windmill = WaterAsset(farm_id=farm.id, name="WM-1", asset_type="windmill", active=True)
        tank_a = WaterAsset(farm_id=farm.id, name="Tank A", asset_type="tank", active=True)
        tank_b = WaterAsset(farm_id=farm.id, name="Tank B", asset_type="tank", active=True)
        trough = WaterAsset(farm_id=farm.id, name="Trough A", asset_type="trough", active=True)
        db.session.add_all([borehole, windmill, tank_a, tank_b, trough])
        db.session.commit()

        farm_id = str(farm.id)
        paddock_id = str(paddock.id)
        borehole_id = str(borehole.id)
        windmill_id = str(windmill.id)
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
    assert "Only trough assets can serve paddocks" in response.get_json()["error"]

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
    assert "gravity only allows cement_dam|tank -> trough" in response.get_json()["error"]

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
            "source_asset_id": borehole_id,
            "destination_asset_id": tank_b_id,
            "pump_asset_id": windmill_id,
            "active": True,
        },
    )
    assert response.status_code == 400
    assert "pump_asset_id is already used by another pumped connection" in response.get_json()["error"]

    get_response = client.get(f"/api/water-connections/{connection_id}")
    assert get_response.status_code == 200
    payload = get_response.get_json()
    assert payload["flow_type"] == "pumped"
    assert payload["pump_asset_id"] == windmill_id


def test_farm_map_data_returns_water_features_with_and_without_kml(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Mapped Water Farm", timezone="UTC")
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
        farm = Farm(name="No Kml Water Farm", timezone="UTC")
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


def test_farm_water_workspace_renders_map_filters_and_satellite_mode(client, app):
    with app.app_context():
        farm = Farm(name="Filtered Water Farm", timezone="UTC")
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


def test_paddock_detail_shows_local_assets_and_serving_troughs(client, app):
    with app.app_context():
        farm = Farm(name="Paddock Water Farm", timezone="UTC")
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
        db.session.add_all([tank, trough])
        db.session.flush()
        db.session.add(WaterAssetServedPaddock(water_asset_id=trough.id, paddock_id=north.id))
        db.session.commit()
        paddock_id = str(north.id)

    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Water For This Paddock" in body
    assert "North Tank" in body
    assert "South Trough" in body
    assert "Open Farm Water Workspace" in body
