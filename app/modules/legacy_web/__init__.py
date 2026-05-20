from flask import Flask

from app.modules.legacy_web.routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp)

