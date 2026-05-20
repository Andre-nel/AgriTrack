from flask import Flask

from app.modules.farms.api_routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp, url_prefix="/api/farms")

