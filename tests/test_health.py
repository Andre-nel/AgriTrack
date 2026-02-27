from app import create_app
from app.config import TestingConfig
from app.extensions import db


def test_health_endpoint():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()

    client = app.test_client()
    response = client.get('/health')

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
