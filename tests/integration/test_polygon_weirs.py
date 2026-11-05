from datetime import date, datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from app.extensions import db
from app.models import Farm, Paddock, User, UserFarmRole, WaterAsset, WaterAssetStateHistory, WaterConnection
from app.modules.analytics.services import build_water_asset_state_report
from app.services.farm_import_service import FarmImportService
from app.services.water_network_service import WaterNetworkService as Water
from app.services import weir_volume_service as volume


@pytest.fixture
def farm(app):
    item = Farm(name="Rietfontein")
    db.session.add(item)
    db.session.commit()
    return item


def source_kml(*, polygon=True, point=True, reverse=False, changed=False):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(Path(__file__).parents[1] / "fixtures/rietfontein_weir.kml").getroot()
    document = root.find("k:Document", ns)
    placemarks = list(document)
    for mark in placemarks:
        if (not polygon and mark.find("k:Polygon", ns) is not None) or (not point and mark.find("k:Point", ns) is not None):
            document.remove(mark)
        elif changed and mark.find("k:Polygon", ns) is not None:
            coordinates = mark.find(".//k:coordinates", ns)
            coordinates.text = coordinates.text.replace("25.41995470546404", "25.41995070546404")
    if reverse:
        document[:] = list(reversed(list(document)))
    paddock = ET.fromstring('''<Placemark xmlns="http://www.opengis.net/kml/2.2"><name>Huiskamp</name><Polygon><outerBoundaryIs><LinearRing><coordinates>25.41,-32.94 25.43,-32.94 25.43,-32.92 25.41,-32.92 25.41,-32.94</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>''')
    document.append(paddock)
    return ET.tostring(root)


@pytest.mark.parametrize("reverse", [False, True])
def test_polygon_upgrades_point_in_place(farm, tmp_path, reverse):
    asset = Water.create_asset(dict(farm_id=farm.id, name="Huiskamp Boonste Weir", asset_type="weir", water_level="half",
                                   import_placemark_name="Huiskamp Boonste Weir", capacity_m3=123))
    other = Water.create_asset(dict(farm_id=farm.id, name="Trough", asset_type="trough", water_level="low"))
    db.session.add(WaterConnection(farm_id=farm.id, source_asset_id=asset.id, destination_asset_id=other.id, flow_type="gravity", active=True))
    db.session.commit()
    identity = asset.id
    history = [row.id for row in WaterAssetStateHistory.query.filter_by(water_asset_id=identity)]
    result = FarmImportService.import_farm(file_name="Rietfontein.kml", file_bytes=source_kml(reverse=reverse), timezone="SAST", instance_path=tmp_path)
    assert result["water_asset_count"] == 1
    assert WaterAsset.query.filter_by(asset_type="weir").count() == 1
    assert Paddock.query.count() == 1
    assert Paddock.query.one().name == "Huiskamp"
    db.session.refresh(asset)
    assert asset.id == identity and asset.weir_polygon["type"] == "Polygon"
    assert asset.water_level is None and asset.water_level_percent == 50
    assert asset.capacity_m3 is None and asset.weir_wall_height_m == 1
    assert float(asset.latitude) == pytest.approx(-32.9272525, abs=0.0000001)
    assert [row.id for row in WaterAssetStateHistory.query.filter_by(water_asset_id=identity)] == history
    assert WaterConnection.query.one().source_asset_id == identity
    outline = asset.weir_polygon
    for contents in (source_kml(), source_kml(polygon=False)):
        FarmImportService.import_farm(file_name="Rietfontein.kml", file_bytes=contents, timezone="SAST", instance_path=tmp_path)
        db.session.refresh(asset)
        assert asset.weir_polygon == outline and asset.id == identity


def test_polygon_only_is_supported_and_conflicts_are_rejected(farm, tmp_path):
    parsed = FarmImportService.parse_upload("Rietfontein.kml", source_kml(point=False))
    assert len(parsed["water_assets"]) == 1
    for _ in range(2):
        Water.create_asset(dict(farm_id=farm.id, name="Huiskamp Boonste Weir", asset_type="weir"))
    db.session.commit()
    with pytest.raises(ValueError, match="More than one existing weir"):
        FarmImportService.import_farm(file_name="Rietfontein.kml", file_bytes=source_kml(), timezone="SAST", instance_path=tmp_path)
    db.session.rollback()
    assert Paddock.query.count() == 0
    assert not (tmp_path / "maps/Rietfontein.kml").exists()


@pytest.mark.parametrize("level", [-1, 101, "nan", "Infinity", True, "50.001"])
def test_percentage_validation_is_atomic(client, farm, level):
    response = client.post("/api/water-assets", json=dict(farm_id=farm.id, name="W", asset_type="weir", water_level_percent=level))
    assert response.status_code == 400
    assert WaterAsset.query.count() == WaterAssetStateHistory.query.count() == 0


def test_percentage_overflow_history_and_unknown(client, farm):
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", water_level="low"))
    db.session.commit()
    endpoint = f"/api/water-assets/{asset.id}"
    for payload, expected, overflow in [({"water_level_percent": 37.25}, 37.25, False), ({"is_overflowing": True}, 100, True), ({"water_level_percent": 100}, 100, False), ({"water_level_percent": None}, None, False)]:
        response = client.patch(endpoint, json=payload)
        assert response.status_code == 200, response.json
        assert response.json["water_level_percent"] == expected
        assert response.json["is_overflowing"] is overflow
    rows = WaterAssetStateHistory.query.filter_by(water_asset_id=asset.id).order_by(WaterAssetStateHistory.changed_at, WaterAssetStateHistory.id).all()
    assert len(rows) == 5
    assert all(row.water_level is None for row in rows)
    assert client.patch(endpoint, json={"water_level_percent": 50, "is_overflowing": True}).status_code == 400
    assert client.patch(endpoint, json={"water_level": "low", "water_level_percent": 70}).status_code == 400


def test_web_and_bulk_record_percentages(client, farm):
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir"))
    db.session.commit()
    response = client.post(f"/farms/{farm.id}/water/assets/{asset.id}", data=dict(name="W", asset_type="weir", active="1", water_level_percent="42.75", is_overflowing="0"))
    assert response.status_code == 302
    db.session.refresh(asset)
    assert float(asset.water_level_percent) == 42.75
    response = client.post(f"/farms/{farm.id}/water/mass-update", data={"asset_type": "weir", "asset_id": asset.id, f"name__{asset.id}": "W", f"active__{asset.id}": "1", f"needs_review__{asset.id}": "0", f"water_level_percent__{asset.id}": "100", f"is_overflowing__{asset.id}": "1"})
    assert response.status_code == 302
    db.session.refresh(asset)
    assert asset.water_level_percent == 100 and asset.is_overflowing


def test_weir_fed_trough_preserves_reported_level_for_mixed_and_dry_sources(farm):
    weir = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", water_level_percent=0))
    tank = Water.create_asset(dict(farm_id=farm.id, name="Tank", asset_type="tank", water_level="full"))
    trough = Water.create_asset(dict(farm_id=farm.id, name="T", asset_type="trough", status="operational", water_level="half"))
    links = [WaterConnection(farm_id=farm.id, source_asset_id=a.id, destination_asset_id=trough.id, flow_type="gravity", active=True) for a in (weir, tank)]
    db.session.add_all(links)
    db.session.commit()
    assert Water.network_state_for_farm(farm.id)["effective_water_levels"][trough.id] == "half"
    tank.water_level = "empty"
    state = Water.network_state_for_farm(farm.id)
    assert state["effective_water_levels"][trough.id] == "half"
    assert "empty or unavailable" in state["asset_warnings"][trough.id]
    weir.active = False
    assert Water.network_state_for_farm(farm.id)["effective_water_levels"][trough.id] == "half"
    links[0].active = False
    assert Water.network_state_for_farm(farm.id)["effective_water_levels"][trough.id] == "empty"


def test_mixed_analytics_has_separate_axes_and_overflow_flag(farm):
    assets = [Water.create_asset(dict(farm_id=farm.id, name=kind, asset_type=kind, water_level="overflowing")) for kind in ("weir", "ground_dam")]
    for row in WaterAssetStateHistory.query.all():
        row.changed_at = datetime(2026, 1, 1)
    db.session.commit()
    report = build_water_asset_state_report(assets=assets, start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), state_fields=["water_level"], plot_mode="overlay", include_farm_name=False)
    panels = report["chart_payload"]["panels"]
    assert len(panels) == 2
    numeric = next(p for p in panels if p.get("numeric_percentage"))
    assert numeric["y_max"] == 100 and numeric["datasets"][0]["overflowing"] == [True, True]
    assert next(r for r in report["summary_rows"] if r["asset_type_label"] == "Weir")["half_or_better_days"] == 2


def test_geometry_setup_preview_map_and_changed_outline(client, farm):
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.0001], [0, 0.0001], [0, 0]]]}
    line = {"type": "LineString", "coordinates": [[0, 0.00005], [0.001, 0.00005]]}
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", weir_polygon=polygon, water_level_percent=50))
    db.session.commit()
    endpoint = f"/api/water-assets/{asset.id}"
    setup = dict(weir_centerline=line, weir_wall_height_m=1, weir_height_assumed=True)
    preview = client.post(endpoint + "/volume-preview", json={**setup, "water_level_percent": 25})
    assert preview.status_code == 200 and preview.json["capacity_m3"] > 0
    db.session.refresh(asset)
    assert asset.weir_centerline is None and asset.water_level_percent == 50
    saved = client.patch(endpoint, json=setup)
    assert saved.status_code == 200, saved.json
    db.session.refresh(asset)
    assert float(asset.capacity_m3) == saved.json["capacity_m3"]
    assert "estimated_capacity_m3" not in saved.json
    assert saved.json["estimated_volume_m3"] == pytest.approx(saved.json["capacity_m3"] / 4, abs=0.02)
    assert WaterAssetStateHistory.query.filter_by(water_asset_id=asset.id).count() == 1
    assert client.get(saved.json["weir_geometry_url"]).status_code == 200
    feature = next(f for f in client.get(f"/farms/{farm.id}/map-data").json["features"] if f["properties"].get("id") == asset.id)
    assert feature["geometry"]["type"] == "Polygon"
    # Reversed winding/start vertex is the same geometry and preserves setup.
    same = {"type": "Polygon", "coordinates": [list(reversed(polygon["coordinates"][0]))]}
    assert client.patch(endpoint, json={"weir_polygon": same}).json["volume_estimate_status"] == "preliminary"
    changed = {"type": "Polygon", "coordinates": [[[0, 0], [0.0011, 0], [0.0011, 0.0001], [0, 0.0001], [0, 0]]]}
    revised = client.patch(endpoint, json={"weir_polygon": changed})
    assert revised.json["volume_estimate_status"] == "review_required"
    assert revised.json["capacity_m3"] is None
    db.session.refresh(asset)
    assert asset.capacity_m3 is None


def geometry_config(response):
    assert response.status_code == 200
    return json.loads(response.get_data(as_text=True).split('<script id="weir-geometry-config" type="application/json">', 1)[1].split("</script>", 1)[0])


def test_geometry_load_suggests_defaults_without_saving_and_preserves_saved_line(client, farm):
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.0001], [0, 0.0001], [0, 0]]]}
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", weir_polygon=polygon,
                                   longitude=-0.00001, latitude=0.00004, water_level_percent=50))
    db.session.commit()
    path = f"/farms/{farm.id}/water/assets/{asset.id}/geometry"
    config = geometry_config(client.get(path))
    defaults = config["defaults"]
    assert defaults["wall"] == pytest.approx([0, 0.00004])
    assert defaults["centerline"]["coordinates"][-1] == pytest.approx([0.001, 0.0001])
    db.session.refresh(asset)
    assert asset.weir_centerline is None and asset.capacity_m3 is None
    assert float(asset.longitude) == -0.00001 and float(asset.latitude) == 0.00004
    endpoint = f"/api/water-assets/{asset.id}"
    setup = {"weir_centerline": defaults["centerline"], "weir_wall_height_m": 1, "weir_height_assumed": True}
    preview = client.post(endpoint + "/volume-preview", json=setup)
    assert preview.status_code == 200 and preview.json["capacity_m3"] > 0
    db.session.refresh(asset)
    assert asset.weir_centerline is None
    saved = client.patch(endpoint, json={**setup, "longitude": defaults["wall"][0], "latitude": defaults["wall"][1]})
    assert saved.status_code == 200, saved.json
    reloaded = geometry_config(client.get(path))
    assert reloaded["defaults"] is None
    assert reloaded["asset"]["weir_centerline"] == saved.json["weir_centerline"]
    assert WaterAssetStateHistory.query.filter_by(water_asset_id=asset.id).count() == 1
    # A changed outline awaiting review must still show the user's saved line.
    asset.weir_geometry_review_required = True
    db.session.commit()
    review = geometry_config(client.get(path))
    assert review["defaults"] is None
    assert review["asset"]["weir_centerline"] == saved.json["weir_centerline"]
    assert review["asset"]["weir_geometry_review_required"] is True


def test_geometry_load_without_point_or_polygon_keeps_manual_setup(client, farm):
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.0001], [0, 0.0001], [0, 0]]]}
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", weir_polygon=polygon))
    point_only = Water.create_asset(dict(farm_id=farm.id, name="Point only", asset_type="weir", longitude=0, latitude=0))
    db.session.commit()
    config = geometry_config(client.get(f"/farms/{farm.id}/water/assets/{asset.id}/geometry"))
    assert config["defaults"]["wall"] is config["defaults"]["centerline"] is None
    assert "No weir point" in config["defaults"]["message"]
    response = client.get(f"/farms/{farm.id}/water/assets/{point_only.id}/geometry")
    assert geometry_config(response)["defaults"] is None
    assert b"Import a WR polygon" in response.data
    # Zero-valued coordinates are available hints too.
    Water.update_asset(asset, {"longitude": 0, "latitude": 0})
    db.session.commit()
    config = geometry_config(client.get(f"/farms/{farm.id}/water/assets/{asset.id}/geometry"))
    assert config["defaults"]["wall"] == pytest.approx([0, 0])
    assert config["defaults"]["centerline"] is not None


def test_water_fill_preview_is_optional_validated_and_does_not_record_levels(client, farm):
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.0001], [0, 0.0001], [0, 0]]]}
    line = {"type": "LineString", "coordinates": [[0, 0.00005], [0.001, 0.00005]]}
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", weir_polygon=polygon,
                                   weir_centerline=line, water_level_percent=50))
    db.session.commit()
    endpoint = f"/api/water-assets/{asset.id}"
    assert "water_fill" not in client.get(endpoint).json
    assert "water_fill" not in client.post(endpoint + "/volume-preview", json={}).json
    for level in (0, 25, 100):
        preview = client.post(endpoint + "/volume-preview", json={"include_water_fill": True, "water_level_percent": level})
        assert preview.status_code == 200
        assert preview.json["water_fill"] is not None
        assert bool(preview.json["water_fill"]) is (level > 0)
    for level in (-1, 101, "bad"):
        preview = client.post(endpoint + "/volume-preview", json={"include_water_fill": True, "water_level_percent": level})
        assert preview.status_code == 400
        assert "water_fill" not in preview.json
    invalid_line = {"type": "LineString", "coordinates": [[0, 0.00005], [0.0005, 0.001], [0.001, 0.00005]]}
    invalid = client.post(endpoint + "/volume-preview", json={"include_water_fill": True, "weir_centerline": invalid_line})
    assert invalid.status_code == 400
    db.session.refresh(asset)
    assert asset.water_level_percent == 50
    assert asset.weir_centerline == volume.validate_centerline(asset.weir_polygon, line)
    assert WaterAssetStateHistory.query.filter_by(water_asset_id=asset.id).count() == 1


def test_non_weir_rejects_even_zero_percentage(client, farm):
    response = client.post("/api/water-assets", json=dict(farm_id=farm.id, name="Tank", asset_type="tank", water_level_percent=0))
    assert response.status_code == 400


def test_import_name_precedence_and_farm_isolation(farm, tmp_path):
    imported = Water.create_asset(dict(farm_id=farm.id, name="Renamed weir", asset_type="weir",
                                      import_placemark_name="  HUISKAMP   BOONSTE WEIR  "))
    same_display = Water.create_asset(dict(farm_id=farm.id, name="Huiskamp Boonste Weir", asset_type="weir"))
    other_farm = Farm(name="Other farm")
    db.session.add(other_farm)
    db.session.flush()
    other = Water.create_asset(dict(farm_id=other_farm.id, name="Huiskamp Boonste Weir", asset_type="weir"))
    db.session.commit()
    FarmImportService.import_farm("Rietfontein.kml", source_kml(), "SAST", tmp_path)
    assert imported.weir_polygon
    assert same_display.weir_polygon is None and other.weir_polygon is None


def test_conflicting_polygons_and_disconnected_outline_fail_before_import(farm, tmp_path):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(source_kml())
    changed = ET.fromstring(source_kml(changed=True))
    polygon_mark = next(mark for mark in changed.findall(".//k:Placemark", ns) if mark.findtext("k:name", namespaces=ns) == "Huiskamp Boonste Weir" and mark.find("k:Polygon", ns) is not None)
    root.find("k:Document", ns).append(polygon_mark)
    with pytest.raises(ValueError, match="Conflicting"):
        FarmImportService.import_farm("Rietfontein.kml", ET.tostring(root), "SAST", tmp_path)
    assert WaterAsset.query.count() == Paddock.query.count() == 0
    polygon_mark.append(ET.fromstring(ET.tostring(polygon_mark.find("k:Polygon", ns))))
    with pytest.raises(ValueError, match="one connected|disconnected|single"):
        FarmImportService.parse_upload("Rietfontein.kml", ET.tostring(changed))


def test_weir_type_change_requires_explicit_categorical_level(client, farm):
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", water_level_percent=37.25))
    db.session.commit()
    endpoint = f"/api/water-assets/{asset.id}"
    assert client.patch(endpoint, json={"asset_type": "ground_dam"}).status_code == 400
    changed = client.patch(endpoint, json={"asset_type": "ground_dam", "water_level": "half"})
    assert changed.status_code == 200
    assert changed.json["water_level_percent"] is None and changed.json["water_level"] == "half"


def test_mobile_typed_levels_old_queue_and_snapshot(client, farm):
    user = User(email="weir@example.com", name="Recorder", active=True)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir"))
    db.session.commit()
    token = client.post("/api/mobile/v1/auth/login", json={"email": user.email, "password": "password"}).json["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/mobile/v1/bootstrap", headers=headers).json["form_options"]["percentage_water_level_types"] == ["weir"]
    payloads = [{"water_level_percent": 37.25, "is_overflowing": False}, {"water_level": "overflowing"},
                {"water_level": "half"}, {"water_level_percent": 101},
                {"water_level_percent": 50, "is_underflowing": True, "status": "operational"}]
    commands = [{"client_command_id": f"weir-{index}", "farm_id": farm.id, "type": "water_asset_status.update",
                 "payload": {"water_asset_id": asset.id, **payload}} for index, payload in enumerate(payloads)]
    response = client.post("/api/mobile/v1/sync/commands", headers=headers, json={"commands": commands})
    assert [row["status"] for row in response.json["results"]] == ["applied", "applied", "applied", "failed", "applied"]
    snapshot = client.get(f"/api/mobile/v1/farms/{farm.id}/snapshot", headers=headers).json
    current = snapshot["water_assets"][0]
    assert current["water_level_percent"] == 50 and current["water_level"] == "underflowing"
    assert current["is_underflowing"] and current["status"] == "damaged"
    assert current["estimated_volume_m3"] is None
    history = snapshot["water_asset_state_history"]
    assert any(row["water_level_percent"] == 37.25 for row in history)
    assert any(row["is_overflowing"] for row in history)


def test_point_reimport_preserves_confirmed_geometry_and_wall(farm, tmp_path):
    original = volume.validate_polygon({"type": "Polygon", "coordinates": [[[25, -32], [25.001, -32], [25.001, -31.9999], [25, -31.9999], [25, -32]]]})
    path = {"type": "LineString", "coordinates": [[25, -31.99995], [25.001, -31.99995]]}
    weir = Water.create_asset(dict(farm_id=farm.id, name="Huiskamp Boonste Weir", asset_type="weir", weir_polygon=original,
                                  weir_centerline=path, weir_wall_height_m=2, weir_height_assumed=False, latitude=-31.99995, longitude=25))
    db.session.commit()
    configured = (weir.weir_centerline, weir.latitude, weir.longitude, weir.weir_wall_height_m)
    FarmImportService.import_farm("Rietfontein.kml", source_kml(polygon=False), "SAST", tmp_path)
    assert weir.weir_polygon == original and not weir.weir_geometry_review_required
    assert (weir.weir_centerline, weir.latitude, weir.longitude, weir.weir_wall_height_m) == configured
    assert not weir.weir_height_assumed


def test_upstream_weir_through_an_intermediate_asset_keeps_trough_reading(farm):
    weir = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", water_level_percent=0))
    tank = Water.create_asset(dict(farm_id=farm.id, name="Tank", asset_type="tank", water_level="full"))
    trough = Water.create_asset(dict(farm_id=farm.id, name="T", asset_type="trough", water_level="low"))
    db.session.add_all([WaterConnection(farm_id=farm.id, source_asset_id=a.id, destination_asset_id=b.id, flow_type="gravity", active=True)
                        for a, b in ((weir, tank), (tank, trough))])
    db.session.commit()
    assert Water.network_state_for_farm(farm.id)["effective_water_levels"][trough.id] == "low"


def test_polygon_capacity_is_canonical_and_cannot_be_manually_overridden(client, farm):
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.0001], [0, 0.0001], [0, 0]]]}
    line = {"type": "LineString", "coordinates": [[0, 0.00005], [0.001, 0.00005]]}
    asset = Water.create_asset(dict(farm_id=farm.id, name="W", asset_type="weir", weir_polygon=polygon,
                                   weir_centerline=line, water_level_percent=50, capacity_m3=123))
    db.session.commit()
    capacity = float(asset.capacity_m3)
    assert capacity != 123
    endpoint = f"/api/water-assets/{asset.id}"
    response = client.patch(endpoint, json={"capacity_m3": 99999})
    assert response.status_code == 200
    assert response.json["capacity_m3"] == capacity
    response = client.patch(endpoint, json={"weir_wall_height_m": 2})
    assert response.json["capacity_m3"] == pytest.approx(capacity * 2, abs=0.02)
    db.session.refresh(asset)
    assert float(asset.capacity_m3) == response.json["capacity_m3"]
    response = client.patch(endpoint, json={"water_level_percent": None})
    assert response.json["capacity_m3"] == float(asset.capacity_m3)
    assert response.json["estimated_volume_m3"] is None
    feature = Water.asset_map_feature(asset)
    assert feature["properties"]["capacity_m3"] == response.json["capacity_m3"]
    assert "estimated_capacity_m3" not in feature["properties"]
    for kind in ("weir", "tank"):
        manual = Water.create_asset(dict(farm_id=farm.id, name=kind, asset_type=kind, capacity_m3=123))
        assert Water.serialize_asset(manual)["capacity_m3"] == 123
        Water.update_asset(manual, {"capacity_m3": 456})
        assert Water.serialize_asset(manual)["capacity_m3"] == 456
