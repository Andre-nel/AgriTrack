from flask import Flask

from app.modules.incidents.routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp)

