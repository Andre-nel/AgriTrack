import sqlite3
import zipfile

import pytest
from sqlalchemy import text

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import User
from scripts.backup_agritrack import create_backup


class AuthRequiredTestingConfig(TestingConfig):
    WEB_AUTH_REQUIRED = True


@pytest.fixture
def auth_client():
    app = create_app(AuthRequiredTestingConfig)
    with app.app_context():
        db.create_all()
        user = User(email="owner@example.com", name="Farm Owner", active=True)
        user.set_password("correct-password")
        db.session.add(user)
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_private_web_routes_require_login_but_health_and_mobile_stay_public(auth_client):
    response = auth_client.get("/")
    assert response.status_code == 302
    assert "/login?next=/" in response.headers["Location"]

    api_response = auth_client.get("/api/farms")
    assert api_response.status_code == 401
    assert api_response.get_json()["error"]["code"] == "authentication_required"

    health_response = auth_client.get("/health")
    assert health_response.status_code == 200

    api_health_response = auth_client.get("/api/v1/health")
    assert api_health_response.status_code == 200

    mobile_response = auth_client.get("/api/mobile/v1/bootstrap")
    assert mobile_response.status_code == 401
    assert mobile_response.get_json()["error"]["code"] == "missing_token"


def test_web_login_and_logout(auth_client):
    login_response = auth_client.post(
        "/login",
        data={
            "email": "owner@example.com",
            "password": "correct-password",
            "next": "/",
        },
    )
    assert login_response.status_code == 302
    assert login_response.headers["Location"] == "/"

    dashboard_response = auth_client.get("/")
    assert dashboard_response.status_code == 200
    assert b"Farm Owner" in dashboard_response.data

    logout_response = auth_client.post("/logout")
    assert logout_response.status_code == 302
    assert "/login" in logout_response.headers["Location"]

    protected_response = auth_client.get("/")
    assert protected_response.status_code == 302


def test_sqlite_connections_enable_foreign_keys(app):
    with app.app_context():
        enabled = db.session.execute(text("PRAGMA foreign_keys")).scalar()

    assert enabled == 1


def test_backup_zip_includes_sqlite_database_maps_and_task_attachments(tmp_path):
    source_db = tmp_path / "agritrack.db"
    connection = sqlite3.connect(source_db)
    try:
        connection.execute("CREATE TABLE farms (name TEXT NOT NULL)")
        connection.execute("INSERT INTO farms (name) VALUES ('Backup Farm')")
        connection.commit()
    finally:
        connection.close()

    instance_dir = tmp_path / "instance"
    maps_dir = instance_dir / "maps"
    attachments_dir = instance_dir / "task_attachments" / "farm-1"
    maps_dir.mkdir(parents=True)
    attachments_dir.mkdir(parents=True)
    (maps_dir / "Backup Farm.kml").write_text("<kml />", encoding="utf-8")
    (attachments_dir / "photo.jpg").write_bytes(b"photo-bytes")

    backup_path = create_backup(
        source_db=source_db,
        instance_dir=instance_dir,
        backup_dir=tmp_path / "backups",
    )

    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
        assert "agritrack.db" in names
        assert "manifest.json" in names
        assert "maps/Backup Farm.kml" in names
        assert "task_attachments/farm-1/photo.jpg" in names
        archive.extract("agritrack.db", tmp_path / "restore")

    restored = sqlite3.connect(tmp_path / "restore" / "agritrack.db")
    try:
        name = restored.execute("SELECT name FROM farms").fetchone()[0]
    finally:
        restored.close()

    assert name == "Backup Farm"
