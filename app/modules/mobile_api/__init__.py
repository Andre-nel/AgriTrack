from flask import Flask

from app.modules.mobile_api.routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp, url_prefix="/api/mobile/v1")
