from flask import Flask

from app.modules.paddocks.api_routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp, url_prefix="/api/paddocks")

