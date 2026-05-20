from flask import Flask

from app.modules.calendar.routes import bp


def register(app: Flask) -> None:
    app.register_blueprint(bp)

