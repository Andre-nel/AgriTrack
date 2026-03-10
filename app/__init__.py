from flask import Flask, jsonify, render_template, request

from app.api.v1.routes import bp as api_v1_bp
from app.blueprints.calendar.routes import bp as calendar_bp
from app.blueprints.farms.routes import bp as farms_api_bp
from app.blueprints.mobs.routes import bp as mobs_api_bp
from app.blueprints.paddocks.routes import bp as paddocks_api_bp
from app.blueprints.reports.routes import bp as web_bp
from app.blueprints.tasks.routes import bp as tasks_bp
from app.cli import init_cli
from app.config import Config, get_config
import app.models  # noqa: F401
from app.extensions import db, migrate


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object or get_config())

    db.init_app(app)
    migrate.init_app(app, db)
    init_cli(app)

    app.register_blueprint(web_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(api_v1_bp, url_prefix="/api/v1")

    # Backward-compatible API prefixes.
    app.register_blueprint(farms_api_bp, url_prefix="/api/farms")
    app.register_blueprint(paddocks_api_bp, url_prefix="/api/paddocks")
    app.register_blueprint(mobs_api_bp, url_prefix="/api/mobs")

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api"):
            return jsonify({"error": "Not found"}), 404
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        if request.path.startswith("/api"):
            return jsonify({"error": "Server error"}), 500
        return render_template("500.html"), 500

    return app
