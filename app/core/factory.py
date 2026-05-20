from flask import Flask

from app.cli import init_cli
from app.config import Config, get_config
from app.core.errors import register_error_handlers
from app.core.health import register_healthcheck
from app.core.modules import register_modules
from app.extensions import db, migrate
import app.models  # noqa: F401


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask("app")
    app.config.from_object(config_object or get_config())

    db.init_app(app)
    migrate.init_app(app, db)
    init_cli(app)

    register_modules(app)
    register_healthcheck(app)
    register_error_handlers(app)

    return app
