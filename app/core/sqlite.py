from pathlib import Path

from flask import Flask
from sqlalchemy.engine import make_url


def sqlite_database_path(app: Flask) -> Path | None:
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None

    path = Path(url.database)
    if not path.is_absolute():
        path = Path(app.instance_path) / path
    return path
