"""Run AgriTrack on a private LAN with Waitress."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from waitress import serve

from app import create_app
from app.config import get_config


def main() -> int:
    host = os.getenv("LAN_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    app = create_app(get_config())
    print(f"Serving AgriTrack on http://{host}:{port}", flush=True)
    serve(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
