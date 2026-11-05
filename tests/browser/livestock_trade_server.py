"""Isolated in-memory server for livestock trade chart browser tests."""

import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from werkzeug.serving import make_server

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm
from app.services.livestock_trade_service import LivestockTradeService


def main():
    app = create_app(TestingConfig)
    document = {
        "schema_version": 1, "import_id": str(uuid4()), "trade_type": "sale",
        "date": "2026-10-15", "counterparty": "Browser test buyer", "species": "Cattle",
        "pricing_model": "weight", "vat_percent": 15,
        "groups": [
            {"label": "Heifers", "breed": "Bonsmara", "sex": "female", "age_class": "young",
             "weights_kg": [250.125, 250.125, 310.875], "price_per_kg": 40},
            {"label": "Bull calves", "breed": "Bonsmara", "sex": "male", "age_class": "calf",
             "weights_kg": [250.125, 280, 300, 295, 315], "price_per_kg": 45},
            {"label": "Heifers", "breed": "Bonsmara", "sex": "female", "age_class": "young",
             "weights_kg": [220 + (index % 17) * 3 for index in range(210)], "price_per_kg": 40},
        ],
    }
    with app.app_context():
        db.create_all()
        farm = Farm(name="Trade chart test farm")
        db.session.add(farm)
        db.session.commit()
        farm_id = farm.id
        trade, _ = LivestockTradeService.import_document(document, [farm_id])
        detail_path = f"/cash-flow/livestock-trades/{trade.id}"
    document["import_id"] = str(uuid4())
    server = make_server("127.0.0.1", 0, app)
    print("TRADE_READY " + json.dumps({
        "port": server.server_port, "path": detail_path, "farm_id": farm_id, "document": document,
    }), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
