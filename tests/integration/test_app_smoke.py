def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"


def test_create_farm_via_api(client):
    response = client.post("/api/farms", json={"name": "Farm A", "timezone": "UTC"})
    assert response.status_code == 201
    assert "id" in response.json


def test_web_dashboard_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data
