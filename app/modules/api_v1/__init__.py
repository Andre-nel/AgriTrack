from flask import Flask

from app.modules.api_v1.routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp, url_prefix="/api/v1")

