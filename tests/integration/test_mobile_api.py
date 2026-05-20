import hashlib
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingSession,
    MobileAuthToken,
    MobileSyncCommand,
    Mob,
    MobEvent,
    Paddock,
    RainfallRecord,
    User,
    UserFarmRole,
    WaterAsset,
    WaterConnection,
)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_user(email: str, password: str, farm: Farm) -> User:
    user = User(email=email, name="Mobile User", active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))
    db.session.flush()
    return user


def _login(client, email: str = "mobile@example.com", password: str = "correct-password") -> str:
    response = client.post(
        "/api/mobile/v1/auth/login",
        json={"email": email, "password": password, "device_name": "Pixel Field Phone"},
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_mobile_login_bootstrap_logout_and_revocation(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Auth Farm", timezone="Africa/Johannesburg", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        db.session.commit()

    login_response = client.post(
        "/api/mobile/v1/auth/login",
        json={
            "email": "mobile@example.com",
            "password": "correct-password",
            "device_name": "Pixel Field Phone",
        },
    )
    assert login_response.status_code == 200
    login_payload = login_response.get_json()
    token = login_payload["token"]
    assert login_payload["token_type"] == "Bearer"
    assert login_payload["user"]["email"] == "mobile@example.com"
    assert [farm["name"] for farm in login_payload["farms"]] == ["Mobile Auth Farm"]

    bootstrap_response = client.get("/api/mobile/v1/bootstrap", headers=_auth(token))
    assert bootstrap_response.status_code == 200
    assert bootstrap_response.get_json()["user"]["email"] == "mobile@example.com"

    logout_response = client.post("/api/mobile/v1/auth/logout", headers=_auth(token))
    assert logout_response.status_code == 200

    revoked_response = client.get("/api/mobile/v1/bootstrap", headers=_auth(token))
    assert revoked_response.status_code == 401
    assert revoked_response.get_json()["error"]["code"] == "revoked_token"


def test_mobile_rejects_invalid_login_missing_token_and_expired_token(client, app):
    with app.app_context():
        farm = Farm(name="Token Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        user = _create_user("token@example.com", "correct-password", farm)
        expired_token = "expired-token"
        db.session.add(
            MobileAuthToken(
                user_id=user.id,
                token_hash=_token_hash(expired_token),
                token_prefix=expired_token[:12],
                device_name="Old phone",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        db.session.commit()

    invalid_login = client.post(
        "/api/mobile/v1/auth/login",
        json={"email": "token@example.com", "password": "wrong"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.get_json()["error"]["code"] == "invalid_credentials"

    missing_token = client.get("/api/mobile/v1/bootstrap")
    assert missing_token.status_code == 401
    assert missing_token.get_json()["error"]["code"] == "missing_token"

    expired = client.get("/api/mobile/v1/bootstrap", headers=_auth(expired_token))
    assert expired.status_code == 401
    assert expired.get_json()["error"]["code"] == "expired_token"


def test_mobile_farm_access_blocks_unassigned_farms(client, app):
    with app.app_context():
        allowed = Farm(name="Allowed Mobile Farm", timezone="UTC", active=True)
        blocked = Farm(name="Blocked Mobile Farm", timezone="UTC", active=True)
        db.session.add_all([allowed, blocked])
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", allowed)
        blocked_id = str(blocked.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{blocked_id}/snapshot", headers=_auth(token))

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"


def test_mobile_snapshot_includes_field_ops_data(client, app):
    with app.app_context():
        farm = Farm(name="Snapshot Mobile Farm", timezone="Africa/Johannesburg", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)

        paddock = Paddock(
            farm_id=farm.id,
            name="North Camp",
            area_ha=20,
            grazeable_area_ha=18,
        )
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        group = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add_all([paddock, mob, group])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=12)
        )
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime.now(timezone.utc),
            end_at=None,
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=datetime.now(timezone.utc).date(), mm=14)
        )
        db.session.add(
            MobEvent(
                farm_id=farm.id,
                mob_id=mob.id,
                tags_csv="health,field note",
                description="Checked in the field",
            )
        )
        tank = WaterAsset(
            farm_id=farm.id,
            name="Header Tank",
            asset_type="tank",
            active=True,
            status="operational",
            water_level="high",
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="North Trough",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="full",
            location_paddock_id=paddock.id,
        )
        db.session.add_all([tank, trough])
        db.session.flush()
        db.session.add(
            WaterConnection(
                farm_id=farm.id,
                flow_type="gravity",
                source_asset_id=tank.id,
                destination_asset_id=trough.id,
            )
        )
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["farm"]["name"] == "Snapshot Mobile Farm"
    assert [paddock["name"] for paddock in payload["paddocks"]] == ["North Camp"]
    assert payload["mobs"][0]["balances"][0]["head_count"] == 12
    assert payload["active_grazing"][0]["allocations"][0]["allocation_fraction"] == 1.0
    assert payload["rainfall"][0]["mm"] == 14.0
    assert payload["mob_events"][0]["tags"] == ["health", "field note"]
    assert {asset["name"] for asset in payload["water_assets"]} == {"Header Tank", "North Trough"}
    assert payload["water_connections"][0]["flow_type"] == "gravity"


def test_mobile_sync_commands_apply_and_duplicate_replay_is_idempotent(client, app):
    with app.app_context():
        farm = Farm(name="Sync Mobile Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        source = Paddock(farm_id=farm.id, name="Source Camp", area_ha=10, grazeable_area_ha=9)
        destination = Paddock(
            farm_id=farm.id,
            name="Destination Camp",
            area_ha=12,
            grazeable_area_ha=10,
        )
        mob = Mob(farm_id=farm.id, name="Sync Mob", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        tank = WaterAsset(
            farm_id=farm.id,
            name="Sync Tank",
            asset_type="tank",
            active=True,
            status="operational",
            water_level="low",
        )
        db.session.add_all([source, destination, mob, group, tank])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=5)
        )
        farm_id = str(farm.id)
        destination_id = str(destination.id)
        mob_id = str(mob.id)
        group_id = str(group.id)
        tank_id = str(tank.id)
        db.session.commit()

    token = _login(client)
    commands = [
        {
            "client_command_id": "rain-1",
            "type": "rainfall.create",
            "farm_id": farm_id,
            "payload": {"recorded_on": "2026-05-18", "mm": 8.5, "note": "Front moved in"},
        },
        {
            "client_command_id": "event-1",
            "type": "mob_event.create",
            "farm_id": farm_id,
            "payload": {
                "mob_id": mob_id,
                "tags": ["condition", "field"],
                "description": "Mob looks settled",
            },
        },
        {
            "client_command_id": "count-1",
            "type": "stock_count.record",
            "farm_id": farm_id,
            "payload": {
                "mob_id": mob_id,
                "animal_group_type_id": group_id,
                "quantity": 7,
                "note": "Mobile count",
            },
        },
        {
            "client_command_id": "move-1",
            "type": "mob.move",
            "farm_id": farm_id,
            "payload": {
                "mob_id": mob_id,
                "allocations": [
                    {"paddock_id": destination_id, "allocation_fraction": 1},
                ],
            },
        },
        {
            "client_command_id": "water-1",
            "type": "water_asset_status.update",
            "farm_id": farm_id,
            "payload": {"water_asset_id": tank_id, "water_level": "full"},
        },
    ]
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": commands},
        headers=_auth(token),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert [result["status"] for result in payload["results"]] == ["applied"] * 5
    assert all(result["duplicate"] is False for result in payload["results"])

    duplicate = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": [commands[0]]},
        headers=_auth(token),
    )
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.get_json()
    assert duplicate_payload["results"][0]["status"] == "applied"
    assert duplicate_payload["results"][0]["duplicate"] is True

    with app.app_context():
        assert RainfallRecord.query.filter_by(farm_id=farm_id).count() == 1
        assert MobEvent.query.filter_by(farm_id=farm_id).count() == 1
        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=group_id,
        ).first()
        assert balance.head_count == 7
        assert GrazingSession.query.filter_by(farm_id=farm_id, mob_id=mob_id, end_at=None).count() == 1
        assert db.session.get(WaterAsset, tank_id).water_level == "full"
        assert MobileSyncCommand.query.filter_by(status="applied").count() == 5


def test_mobile_sync_commands_report_partial_failures_and_missing_records(client, app):
    with app.app_context():
        farm = Farm(name="Partial Sync Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        mob = Mob(farm_id=farm.id, name="Partial Mob", status="active")
        group = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        db.session.add_all([mob, group])
        db.session.flush()
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        group_id = str(group.id)
        db.session.commit()

    token = _login(client)
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "partial-rain-1",
                    "type": "rainfall.create",
                    "farm_id": farm_id,
                    "payload": {"recorded_on": "2026-05-18", "mm": 3},
                },
                {
                    "client_command_id": "partial-count-1",
                    "type": "stock_count.record",
                    "farm_id": farm_id,
                    "payload": {
                        "mob_id": mob_id,
                        "animal_group_type_id": group_id,
                        "quantity": -1,
                    },
                },
                {
                    "client_command_id": "missing-water-1",
                    "type": "water_asset_status.update",
                    "farm_id": farm_id,
                    "payload": {"water_asset_id": "missing", "water_level": "full"},
                },
            ]
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    results = response.get_json()["results"]
    assert [result["status"] for result in results] == ["applied", "failed", "failed"]
    assert results[1]["error"]["code"] == "invalid_command"
    assert results[2]["error"]["code"] == "not_found"

    with app.app_context():
        assert RainfallRecord.query.filter_by(farm_id=farm_id).count() == 1
        assert AnimalGroupBalance.query.filter_by(mob_id=mob_id).count() == 0
        assert MobileSyncCommand.query.filter_by(status="failed").count() == 2
