import json
import re
from datetime import date, datetime, timedelta
from math import degrees
from urllib.parse import parse_qs, urlparse

import pytest

from app.extensions import db
from app.models import Farm, WaterAssetEvent, WaterAssetStateHistory
from app.modules.analytics.services import build_water_asset_state_report
from app.services.polygon_geometry import EARTH_RADIUS_M
from app.services.water_network_service import WaterNetworkService as Water


@pytest.fixture
def weir(app):
    farm = Farm(name="Detail farm", timezone="SAST")
    db.session.add(farm)
    db.session.flush()
    def geo(x, y):
        return [degrees(x / EARTH_RADIUS_M), degrees(y / EARTH_RADIUS_M)]
    asset = Water.create_asset(dict(
        farm_id=farm.id, name="Detail Weir", asset_type="weir", water_level_percent=37.25,
        latitude=0, longitude=0,
        weir_polygon={"type": "Polygon", "coordinates": [[geo(0, 0), geo(100, 0), geo(100, 10), geo(0, 10), geo(0, 0)]]},
        weir_centerline={"type": "LineString", "coordinates": [geo(0, 5), geo(100, 5)]},
    ))
    db.session.commit()
    return asset


def seed_depth_history(asset):
    WaterAssetStateHistory.query.filter_by(water_asset_id=asset.id).delete()
    for day, depth in [(2, 0), (3, 50), (4, None), (5, 100)]:
        db.session.add(WaterAssetStateHistory(
            water_asset_id=asset.id, farm_id=asset.farm_id, changed_at=datetime(2026, 1, day, 8),
            change_type="created" if day == 2 else "updated", active=True,
            water_level_percent=depth, is_overflowing=day == 5,
        ))
    db.session.commit()


def report_for(asset, fields=None):
    return build_water_asset_state_report(
        assets=[asset], start_date=date(2026, 1, 1), end_date=date(2026, 1, 6),
        state_fields=fields or ["water_level", "capacity_m3", "estimated_volume_m3"],
        plot_mode="overlay", include_farm_name=False,
    )


def test_detail_page_has_editor_storage_actions_and_farm_scope(client, weir):
    path = f"/farms/{weir.farm_id}/water/assets/{weir.id}"
    response = client.get(path)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Current information" in body and "Estimated volume (m³)" in body
    assert f'data-inline-asset-id="{weir.id}"' in body
    assert 'name="return_to" value="asset"' in body
    assert 'data-water-asset-connections' in body and 'data-water-asset-task-panel' in body
    assert f'href="{path}/geometry"' in body
    analytics = re.search(r'href="([^"]+)">Asset analytics</a>', body).group(1).replace("&amp;", "&")
    query = parse_qs(urlparse(analytics).query)
    assert query["water_asset_id"] == [weir.id]
    assert query["state_field"] == ["water_level", "capacity_m3", "estimated_volume_m3"]
    assert query["current_active"] == ["all"]
    other = Farm(name="Other")
    db.session.add(other)
    db.session.commit()
    assert client.get(f"/farms/{other.id}/water/assets/{weir.id}").status_code == 404
    workspace = client.get(f"/farms/{weir.farm_id}/water").get_data(as_text=True)
    assert f'href="{path}"' in workspace
    assert 'data-water-asset-navigation="detail"' in workspace
    feature = next(f for f in client.get(f"/farms/{weir.farm_id}/map-data").json["features"] if f["properties"].get("id") == weir.id)
    assert feature["properties"]["asset_detail_url"] == path


def test_detail_edits_notes_and_connections_return_to_asset(client, weir):
    path = f"/farms/{weir.farm_id}/water/assets/{weir.id}"
    form = dict(return_to="asset", name=weir.name, asset_type="weir", active="1", water_level_percent="42.75", is_overflowing="0")
    response = client.post(path, data=form)
    assert response.status_code == 302 and response.location == path
    db.session.refresh(weir)
    assert float(weir.water_level_percent) == 42.75
    invalid = client.post(path, data={**form, "water_level_percent": "101"})
    assert invalid.location == path
    db.session.refresh(weir)
    assert float(weir.water_level_percent) == 42.75
    note = client.post(path + "/events", data={"return_to": "asset", "event_description": "Inspection complete"})
    assert note.location == path + "#asset-notes"
    assert WaterAssetEvent.query.filter_by(water_asset_id=weir.id).one().description == "Inspection complete"
    tank = Water.create_asset(dict(farm_id=weir.farm_id, name="Receiver", asset_type="tank"))
    db.session.commit()
    connection = client.post(f"/farms/{weir.farm_id}/water/connections", data={
        "source_asset_id": weir.id, "destination_asset_id": tank.id, "flow_type": "gravity", "active": "1",
        "open_asset_id": weir.id, "return_to": "asset",
    })
    assert connection.location == path


def test_detail_shows_only_the_latest_fifty_observations(client, weir):
    WaterAssetStateHistory.query.filter_by(water_asset_id=weir.id).delete()
    for day in range(51):
        db.session.add(WaterAssetStateHistory(
            water_asset_id=weir.id, farm_id=weir.farm_id,
            changed_at=datetime(2026, 1, 1, 8) + timedelta(days=day),
            change_type="updated", active=True, water_level_percent=day,
        ))
    db.session.commit()
    body = client.get(f"/farms/{weir.farm_id}/water/assets/{weir.id}").get_data(as_text=True)
    assert "2026-01-01 08:00:00" not in body
    assert "2026-01-02 08:00:00" in body
    assert "2026-02-20 08:00:00" in body
    assert body.index("2026-02-20 08:00:00") < body.index("2026-01-02 08:00:00")
    assert len(Water.state_history_for_asset(weir.id)) == 51


def test_non_weir_detail_uses_relevant_fields_and_no_geometry_action(client, weir):
    pump = Water.create_asset(dict(farm_id=weir.farm_id, name="Solar pump", asset_type="solarpump"))
    db.session.commit()
    response = client.get(f"/farms/{weir.farm_id}/water/assets/{pump.id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Configure weir geometry" not in body
    assert "name=\"solar_kw\"" in body
    assert "state_field=active" in body and "state_field=status" in body


def test_storage_charts_use_current_geometry_with_unknown_gaps_without_writes(weir):
    seed_depth_history(weir)
    before = float(weir.water_level_percent)
    report = report_for(weir)
    panels = {p["field"]: p for p in report["chart_payload"]["panels"]}
    assert panels["water_level"]["datasets"][0]["values"] == [None, 0, 50, None, 100, 100]
    assert panels["estimated_volume_m3"]["datasets"][0]["values"] == [None, 0, 125, None, 500, 500]
    assert panels["capacity_m3"]["datasets"][0]["values"] == [500] * 6
    assert panels["capacity_m3"]["datasets"][0]["reference"] is True
    assert "Historical capacity changes were not recorded" in panels["capacity_m3"]["description"]
    assert "current outline" in panels["estimated_volume_m3"]["description"]
    assert panels["estimated_volume_m3"]["datasets"][0]["overflowing"] == [False, False, False, False, True, True]
    assert not db.session.is_modified(weir)
    assert float(weir.water_level_percent) == before
    assert WaterAssetStateHistory.query.filter_by(water_asset_id=weir.id).count() == 4
    Water.update_asset(weir, {"weir_wall_height_m": 2, "weir_height_assumed": False})
    db.session.commit()
    revised = {p["field"]: p for p in report_for(weir)["chart_payload"]["panels"]}
    assert revised["estimated_volume_m3"]["datasets"][0]["values"] == [None, 0, 250, None, 1000, 1000]
    assert revised["capacity_m3"]["datasets"][0]["values"] == [1000] * 6
    assert WaterAssetStateHistory.query.filter_by(water_asset_id=weir.id).count() == 4


def test_storage_panels_explain_unavailable_geometry_and_do_not_infer_categorical_volumes(weir):
    seed_depth_history(weir)
    weir.weir_geometry_review_required = True
    db.session.commit()
    panels = {p["field"]: p for p in report_for(weir)["chart_payload"]["panels"]}
    assert panels["water_level"]["datasets"]
    assert panels["estimated_volume_m3"]["datasets"] == []
    assert any("review required" in message for message in panels["estimated_volume_m3"]["messages"])
    tank = Water.create_asset(dict(farm_id=weir.farm_id, name="Tank", asset_type="tank", capacity_m3=10, water_level="half"))
    db.session.commit()
    tank_panels = report_for(tank, ["capacity_m3", "estimated_volume_m3"])["chart_payload"]["panels"]
    assert [p["field"] for p in tank_panels] == ["capacity_m3"]
    assert tank_panels[0]["datasets"][0]["values"] == [10] * 6


def test_focused_analytics_sections_are_collapsible_and_invalid_ids_never_broaden_scope(client, weir):
    seed_depth_history(weir)
    query = {"farm_id": weir.farm_id, "water_asset_id": weir.id,
             "state_field": ["water_level", "capacity_m3", "estimated_volume_m3"],
             "start_date": "2026-01-01", "end_date": "2026-01-06"}
    response = client.get("/analytics/water-assets", query_string=query)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Detail Weir Analytics" in body
    assert body.count('<summary><h') == 6
    assert "Weir Depth (%) Over Time" in body
    assert "Current Capacity Reference (m³)" in body
    assert "Estimated Weir Volume (m³) Over Time" in body
    response = client.get("/analytics/water-assets", query_string={**query, "water_asset_id": "missing"})
    payload = json.loads(re.search(r'<script id="waterAssetAnalyticsData" type="application/json">(.*?)</script>', response.get_data(as_text=True), re.S).group(1))
    assert payload["panels"] == []
