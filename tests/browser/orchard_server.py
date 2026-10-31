"""Isolated, in-memory server used only by orchard.cjs browser acceptance tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from werkzeug.serving import make_server

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm
from app.modules.orchards.seed import import_rietfontein


def main():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        farm = Farm(name="Rietfontein")
        db.session.add(farm)
        db.session.flush()
        orchard, _ = import_rietfontein(farm)
        db.session.commit()
        path = f"/farms/{farm.id}/orchards/{orchard.id}"
        api = f"/api/farms/{farm.id}/orchards/{orchard.id}"
    server = make_server("127.0.0.1", 0, app)
    print("ORCHARD_READY " + json.dumps({"port": server.server_port, "path": path, "api": api}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
