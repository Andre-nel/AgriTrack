import json
import re
from datetime import date, datetime

import pytest

from app.extensions import db
from app.models import Farm, Paddock, User, UserFarmRole, WaterAsset, WaterAssetStateHistory, WaterConnection
from app.modules.analytics.services import build_water_asset_state_report
from app.services.water_network_service import WaterNetworkService


@pytest.fixture
def farm_id(app):
    farm = Farm(name="Overflow farm")
    db.session.add(farm)
    db.session.commit()
    return farm.id


@pytest.mark.parametrize("asset_type", WaterNetworkService.ASSET_TYPES)
def test_api_overflowing_is_restricted_to_weirs_and_ground_dams(client, farm_id, asset_type):
    response = client.post("/api/water-assets", json={
        "farm_id": farm_id, "name": "Source", "asset_type": asset_type,
        "water_level": "Overflowing",
    })
    if asset_type not in {"weir", "ground_dam"}:
        assert response.status_code == 400
        assert WaterAsset.query.count() == 0
        return
    assert response.status_code == 201
    asset_id = response.get_json()["id"]
    assert response.get_json()["water_level"] == "overflowing"
    assert WaterAssetStateHistory.query.one().water_level == "overflowing"
    assert client.patch(f"/api/water-assets/{asset_id}", json={"asset_type": "tank"}).status_code == 400
    assert client.patch(f"/api/water-assets/{asset_id}", json={"asset_type": "borehole"}).status_code == 400
    response = client.patch(f"/api/water-assets/{asset_id}", json={"asset_type": "tank", "water_level": "full"})
    assert response.status_code == 200
    assert response.get_json()["water_level"] == "full"


@pytest.mark.parametrize("asset_type", ["weir", "ground_dam", "tank"])
def test_import_validation_uses_type_specific_levels(farm_id, asset_type):
    payload = dict(farm_id=farm_id, name="Imported", asset_type=asset_type, water_level="overflowing")
    if asset_type == "tank":
        with pytest.raises(ValueError, match="water_level"):
            WaterNetworkService.create_asset(payload, imported=True)
    else:
        assert WaterNetworkService.create_asset(payload, imported=True).water_level == "overflowing"


@pytest.mark.parametrize("asset_type", ["weir", "ground_dam", "tank"])
def test_web_create_edit_and_bulk_options(client, farm_id, asset_type):
    valid = asset_type in {"weir", "ground_dam"}
    response = client.post(f"/farms/{farm_id}/water/assets", data={
        "name": "Source", "asset_type": asset_type, "active": "1", "water_level": "overflowing",
    })
    assert response.status_code == 302
    assert WaterAsset.query.count() == int(valid)
    if not valid:
        asset = WaterNetworkService.create_asset(dict(farm_id=farm_id, name="Source", asset_type=asset_type, water_level="full"))
        db.session.commit()
    else:
        asset = WaterAsset.query.one()
    asset_id = asset.id
    page = client.get(f"/farms/{farm_id}/water").get_data(as_text=True)
    config = json.loads(re.search(r'<script id="water-asset-editor-config" type="application/json">(.*?)</script>', page, re.S)[1])
    assert ("overflowing" in config["waterLevelOptionsByType"][asset_type]) == valid
    bulk_page = client.get(f"/farms/{farm_id}/water/mass-update?filters_applied=1&asset_type={asset_type}").get_data(as_text=True)
    options = re.search(rf'<select name="water_level__{asset_id}">(.*?)</select>', bulk_page, re.S)[1]
    assert ('value="overflowing"' in options) == valid
    for endpoint, data in [
        (f"/farms/{farm_id}/water/assets/{asset_id}", {
            "name": "Source", "asset_type": asset_type, "active": "1", "water_level": "overflowing",
        }),
        (f"/farms/{farm_id}/water/mass-update", {
            "asset_type": asset_type, "asset_id": asset_id, f"name__{asset_id}": "Source",
            f"active__{asset_id}": "1", f"water_level__{asset_id}": "overflowing",
        }),
    ]:
        assert client.post(endpoint, data=data).status_code == 302
        db.session.expire_all()
        assert db.session.get(WaterAsset, asset_id).water_level == ("overflowing" if valid else "full")


@pytest.mark.parametrize("asset_type", ["weir", "ground_dam"])
def test_overflowing_source_supplies_full_trough_and_map(client, farm_id, asset_type):
    paddock = Paddock(farm_id=farm_id, name="Water paddock", area_ha=10)
    db.session.add(paddock)
    db.session.flush()
    source = WaterNetworkService.create_asset(dict(farm_id=farm_id, name="Source", asset_type=asset_type,
        water_level="overflowing", status="operational", latitude=-32, longitude=25,
        location_paddock_id=paddock.id))
    trough = WaterNetworkService.create_asset(dict(farm_id=farm_id, name="Trough", asset_type="trough",
        water_level="low", status="operational", latitude=-32.001, longitude=25.001))
    db.session.add(WaterConnection(farm_id=farm_id, flow_type="gravity", source_asset_id=source.id,
                                   destination_asset_id=trough.id, active=True))
    db.session.commit()
    state = WaterNetworkService.network_state_for_farm(farm_id)
    assert state["effective_water_levels"][source.id] == "overflowing"
    assert state["effective_water_levels"][trough.id] == "full"
    assert WaterNetworkService._asset_supply_state(source) == "available"
    payload = client.get(f"/api/water-assets/{trough.id}").get_json()
    assert payload["water_level"] == "full"
    assert payload["reported_water_level"] == "low"
    response = client.get(f"/farms/{farm_id}/map-data")
    assert response.status_code == 200
    assert "overflowing" in response.get_data(as_text=True)


def test_mobile_commands_accept_only_supported_assets(client, farm_id):
    user = User(email="overflow@example.com", name="Field user", active=True)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=farm_id, role="manager"))
    assets = [WaterNetworkService.create_asset(dict(farm_id=farm_id, name=kind, asset_type=kind, water_level="full"))
              for kind in ("weir", "ground_dam", "tank")]
    db.session.commit()
    token = client.post("/api/mobile/v1/auth/login", json={"email": user.email, "password": "password"}).get_json()["token"]
    response = client.post("/api/mobile/v1/sync/commands", headers={"Authorization": f"Bearer {token}"}, json={
        "commands": [{"client_command_id": f"overflow-{asset.asset_type}", "type": "water_asset_status.update",
                      "farm_id": farm_id, "payload": {"water_asset_id": asset.id, "water_level": "overflowing"}}
                     for asset in assets],
    })
    assert response.status_code == 200
    assert [row["status"] for row in response.get_json()["results"]] == ["applied", "applied", "failed"]
    db.session.expire_all()
    assert [asset.water_level for asset in assets] == ["overflowing", "overflowing", "full"]


def test_analytics_places_overflowing_above_full(farm_id):
    asset = WaterNetworkService.create_asset(dict(farm_id=farm_id, name="Weir", asset_type="weir", water_level="overflowing"))
    db.session.flush()
    WaterAssetStateHistory.query.one().changed_at = datetime(2025, 1, 1)
    db.session.commit()
    report = build_water_asset_state_report(assets=[asset], start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 2), state_fields=["water_level"], plot_mode="overlay", include_farm_name=False)
    panel = report["chart_payload"]["panels"][0]
    assert panel["value_labels"]["5"] == "Overflowing"
    assert panel["datasets"][0]["values"] == [5, 5]
