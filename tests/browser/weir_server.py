"""Isolated in-memory server for the weir browser acceptance test."""
import json
import sys
from datetime import datetime
from math import cos, degrees, radians
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from werkzeug.serving import make_server
from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm, WaterAssetStateHistory
from app.services.polygon_geometry import EARTH_RADIUS_M
from app.services.water_network_service import WaterNetworkService


def main():
    app = create_app(TestingConfig)
    def geo(x, y):
        return [25 + degrees(x / (EARTH_RADIUS_M * cos(radians(-32)))), -32 + degrees(y / EARTH_RADIUS_M)]
    with app.app_context():
        db.create_all()
        farm = Farm(name="Browser test farm")
        defaults_farm = Farm(name="Geometry defaults test farm")
        db.session.add_all([farm, defaults_farm])
        db.session.flush()
        weir = WaterNetworkService.create_asset(dict(farm_id=farm.id, name="Test weir", asset_type="weir", water_level_percent=50,
            weir_polygon={"type": "Polygon", "coordinates": [[geo(0, 0), geo(100, 0), geo(100, 10), geo(0, 10), geo(0, 0)]]}))
        initial = WaterAssetStateHistory.query.filter_by(water_asset_id=weir.id).one()
        initial.changed_at = datetime(2026, 1, 1, 8)
        initial.water_level_percent = 0
        previous_depth = 0
        for month, day, depth in [(1, 10, 25), (1, 20, 50), (1, 30, 75), (2, 5, None), (2, 15, 50)]:
            db.session.add(WaterAssetStateHistory(
                water_asset_id=weir.id, farm_id=farm.id, change_type="updated",
                changed_at=datetime(2026, month, day, 8), active=True,
                previous_water_level_percent=previous_depth, water_level_percent=depth, is_overflowing=False,
            ))
            previous_depth = depth
        suggested = WaterNetworkService.create_asset(dict(farm_id=defaults_farm.id, name="Suggested weir", asset_type="weir", water_level_percent=50,
            longitude=geo(-5, 0)[0], latitude=geo(-5, 0)[1],
            weir_polygon={"type": "Polygon", "coordinates": [[geo(0, 0), geo(50, -10), geo(100, 0), geo(50, 10), geo(0, 0)]]}))
        island = WaterNetworkService.create_asset(dict(farm_id=defaults_farm.id, name="Weir with island", asset_type="weir",
            longitude=geo(-5, 0)[0], latitude=geo(-5, 0)[1],
            weir_polygon={"type": "Polygon", "coordinates": [
                [geo(0, 0), geo(50, -10), geo(100, 0), geo(50, 10), geo(0, 0)],
                [geo(45, -2), geo(55, -2), geo(55, 2), geo(45, 2), geo(45, -2)],
            ]}))
        shaded_island = WaterNetworkService.create_asset(dict(farm_id=defaults_farm.id, name="Configured island weir", asset_type="weir",
            water_level_percent=50,
            weir_centerline={"type": "LineString", "coordinates": [geo(0, 5), geo(100, 5)]},
            weir_polygon={"type": "Polygon", "coordinates": [
                [geo(0, 0), geo(100, 0), geo(100, 10), geo(0, 10), geo(0, 0)],
                [geo(20, 1), geo(30, 1), geo(30, 3), geo(20, 3), geo(20, 1)],
            ]}))
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        source = ET.parse(Path(__file__).parents[1] / "fixtures/rietfontein_weir.kml").getroot()
        def coordinates(text):
            return [list(map(float, p.split(",")[:2])) for p in text.split()]
        hint = coordinates(source.find(".//k:Point/k:coordinates", ns).text)[0]
        curved = WaterNetworkService.create_asset(dict(farm_id=defaults_farm.id, name="Curved weir", asset_type="weir", water_level_percent=50,
            longitude=hint[0], latitude=hint[1],
            weir_polygon={"type": "Polygon", "coordinates": [coordinates(source.find(".//k:LinearRing/k:coordinates", ns).text)]}))
        db.session.commit()
        info = {"id": weir.id, "farm": farm.id, "path": f"/farms/{farm.id}/water/assets/{weir.id}/geometry",
                "suggested_id": suggested.id, "suggested_path": f"/farms/{defaults_farm.id}/water/assets/{suggested.id}/geometry",
                "island_path": f"/farms/{defaults_farm.id}/water/assets/{island.id}/geometry",
                "shaded_island_path": f"/farms/{defaults_farm.id}/water/assets/{shaded_island.id}/geometry",
                "curved_path": f"/farms/{defaults_farm.id}/water/assets/{curved.id}/geometry"}
    server = make_server("127.0.0.1", 0, app)
    print("WEIR_READY " + json.dumps({**info, "port": server.server_port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
