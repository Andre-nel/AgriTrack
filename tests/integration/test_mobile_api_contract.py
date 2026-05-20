from pathlib import Path

from app.extensions import db
from app.models import Farm, User, UserFarmRole


def _create_mobile_user() -> None:
    farm = Farm(name="Contract Farm", timezone="UTC", active=True)
    db.session.add(farm)
    db.session.flush()
    user = User(email="contract@example.com", name="Contract User", active=True)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))
    db.session.commit()


def _login(client) -> str:
    response = client.post(
        "/api/mobile/v1/auth/login",
        json={
            "email": "contract@example.com",
            "password": "password",
            "device_name": "Contract Test Phone",
        },
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def test_mobile_api_documented_commands_match_bootstrap_contract(client, app):
    with app.app_context():
        _create_mobile_user()

    docs = Path("docs/mobile_api.md").read_text(encoding="utf-8")
    token = _login(client)
    response = client.get(
        "/api/mobile/v1/bootstrap",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    command_types = response.get_json()["sync"]["supported_command_types"]
    assert command_types == [
        "rainfall.create",
        "mob_event.create",
        "stock_count.record",
        "mob.move",
        "water_asset_status.update",
    ]
    for command_type in command_types:
        assert command_type in docs


def test_mobile_api_error_shape_matches_documentation(client):
    response = client.get("/api/mobile/v1/bootstrap")

    assert response.status_code == 401
    assert response.get_json() == {
        "error": {
            "code": "missing_token",
            "message": "Bearer token is required",
        }
    }
