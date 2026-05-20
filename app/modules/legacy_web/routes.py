from flask import Blueprint

from app.modules.analytics.routes import register_legacy_routes as register_analytics_routes
from app.modules.dashboard.routes import register_legacy_routes as register_dashboard_routes
from app.modules.farms.map_routes import register_legacy_routes as register_farm_map_routes
from app.modules.farms.routes import register_legacy_routes as register_farm_routes
from app.modules.grazing.routes import register_legacy_routes as register_grazing_routes
from app.modules.imports.routes import register_legacy_routes as register_import_routes
from app.modules.mobs.routes import register_legacy_routes as register_mob_routes
from app.modules.paddocks.routes import register_legacy_routes as register_paddock_routes
from app.modules.water.routes import register_legacy_routes as register_water_routes

bp = Blueprint("web", __name__)
register_analytics_routes(bp)
register_dashboard_routes(bp)
register_import_routes(bp)
register_farm_routes(bp)
register_farm_map_routes(bp)
register_grazing_routes(bp)
register_mob_routes(bp)
register_paddock_routes(bp)
register_water_routes(bp)


