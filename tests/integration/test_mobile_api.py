import hashlib
from io import BytesIO
from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    AnimalGroupType,
    CalendarActivity,
    Farm,
    FenceEvent,
    FenceSection,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
    GrazingSession,
    Incident,
    MobileAuthToken,
    MobileSyncCommand,
    Mob,
    MobEvent,
    NoteAttachment,
    Paddock,
    PaddockEvent,
    PaddockGate,
    RainfallRecord,
    StockLedgerEntry,
    Task,
    TaskAttachment,
    TaskComment,
    TaskEntityLink,
    TaskSpace,
    TaskStatusTransition,
    User,
    UserFarmRole,
    WaterAsset,
    WaterAssetEvent,
    WaterAssetStateHistory,
    WaterAssetServedPaddock,
    WaterConnection,
)
from app.services.gate_service import GateService
from app.services.movement_service import MovementService
from app.services.task_service import TaskService


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
    assert "form_options" in bootstrap_response.get_json()

    ping_response = client.get("/api/mobile/v1/ping", headers=_auth(token))
    assert ping_response.status_code == 200
    assert ping_response.get_json()["status"] == "ok"

    logout_response = client.post("/api/mobile/v1/auth/logout", headers=_auth(token))
    assert logout_response.status_code == 200

    revoked_response = client.get("/api/mobile/v1/bootstrap", headers=_auth(token))
    assert revoked_response.status_code == 401
    assert revoked_response.get_json()["error"]["code"] == "revoked_token"


def test_mobile_login_bootstrap_and_ping_include_multiple_farm_roles(client, app):
    with app.app_context():
        alpha = Farm(name="Alpha Mobile Farm", timezone="SAST", active=True)
        beta = Farm(name="Beta Mobile Farm", timezone="Africa/Johannesburg", active=True)
        inactive = Farm(name="Inactive Mobile Farm", timezone="SAST", active=False)
        db.session.add_all([alpha, beta, inactive])
        db.session.flush()
        user = User(email="multi@example.com", name="Multi Farm User", active=True)
        user.set_password("correct-password")
        db.session.add(user)
        db.session.flush()
        db.session.add_all(
            [
                UserFarmRole(user_id=user.id, farm_id=beta.id, role="manager"),
                UserFarmRole(user_id=user.id, farm_id=alpha.id, role="observer"),
                UserFarmRole(user_id=user.id, farm_id=inactive.id, role="manager"),
            ]
        )
        db.session.commit()

    login_response = client.post(
        "/api/mobile/v1/auth/login",
        json={
            "email": "multi@example.com",
            "password": "correct-password",
            "device_name": "Pixel Field Phone",
        },
    )
    assert login_response.status_code == 200
    token = login_response.get_json()["token"]
    expected = [
        {"name": "Alpha Mobile Farm", "role": "observer"},
        {"name": "Beta Mobile Farm", "role": "manager"},
    ]
    assert [
        {"name": farm["name"], "role": farm["role"]}
        for farm in login_response.get_json()["farms"]
    ] == expected

    bootstrap_response = client.get("/api/mobile/v1/bootstrap", headers=_auth(token))
    assert bootstrap_response.status_code == 200
    assert [
        {"name": farm["name"], "role": farm["role"]}
        for farm in bootstrap_response.get_json()["farms"]
    ] == expected

    ping_response = client.get("/api/mobile/v1/ping", headers=_auth(token))
    assert ping_response.status_code == 200
    assert [
        {"name": farm["name"], "role": farm["role"]}
        for farm in ping_response.get_json()["farms"]
    ] == expected


def test_mobile_rejects_invalid_login_missing_token_and_expired_token(client, app):
    with app.app_context():
        farm = Farm(name="Token Farm", timezone="SAST", active=True)
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
        allowed = Farm(name="Allowed Mobile Farm", timezone="SAST", active=True)
        blocked = Farm(name="Blocked Mobile Farm", timezone="SAST", active=True)
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
        grazing_started_at = datetime.now(timezone.utc) - timedelta(days=3)
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=grazing_started_at,
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
        db.session.add(
            PaddockEvent(
                farm_id=farm.id,
                paddock_id=paddock.id,
                tags_csv="pasture,field note",
                description="Pasture recovering after rain",
            )
        )
        space = TaskSpace(
            farm_id=farm.id,
            key="MOBSNAP",
            name="Mobile Snapshot",
            description="Field work",
        )
        db.session.add(space)
        db.session.flush()
        task = TaskService.create_task(
            space=space,
            heading="Check north trough",
            description="Confirm water level and float valve.",
            raw_tags="water,field",
            reporter_name="Mobile User",
            assignee_name="Field Team",
            status="todo",
            priority="high",
            original_estimate_days=None,
            due_date=datetime.now(timezone.utc).date().isoformat(),
        )
        db.session.flush()
        fence = FenceSection(
            farm_id=farm.id,
            section_key="boundary:north-camp",
            name="North Camp Boundary Fence",
            source=FenceSection.SOURCE_AUTO,
            section_type=FenceSection.TYPE_BOUNDARY,
            paddock_a_id=paddock.id,
            condition="bad",
            height_profile="low",
            construction_type="mesh",
            mesh_type="wire_mesh",
            electric_wire=True,
            length_m=240,
        )
        db.session.add(fence)
        db.session.flush()
        db.session.add(
            FenceEvent(
                farm_id=farm.id,
                fence_section_id=fence.id,
                event_type="inspection",
                tags_csv="fence,field note",
                condition_after="bad",
                description="Jackal hole under boundary fence",
            )
        )
        db.session.add(
            incident := Incident(
                farm_id=farm.id,
                occurred_on=datetime.now(timezone.utc).date(),
                category="Stock missing",
                note="Two ewes missing from North Camp",
                tags_csv="stock,security",
                reported_by="Field Team",
            )
        )
        TaskService.add_entity_links(
            task=task,
            paddock_ids=[paddock.id],
            mob_ids=[mob.id],
            fence_section_ids=[fence.id],
        )
        db.session.add(
            activity := CalendarActivity(
                farm_id=farm.id,
                title="Weekly water run",
                description="Check tanks and troughs.",
                start_date=datetime.now(timezone.utc).date(),
                duration_days=1,
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
            WaterAssetEvent(
                farm_id=farm.id,
                water_asset_id=trough.id,
                tags_csv="inspection,field note",
                description="Float valve checked on water run",
            )
        )
        db.session.add(
            WaterConnection(
                farm_id=farm.id,
                flow_type="gravity",
                source_asset_id=tank.id,
                destination_asset_id=trough.id,
            )
        )
        farm_id = str(farm.id)
        task_id = str(task.id)
        activity_id = str(activity.id)
        incident_id = str(incident.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["farm"]["name"] == "Snapshot Mobile Farm"
    assert [paddock["name"] for paddock in payload["paddocks"]] == ["North Camp"]
    assert payload["mobs"][0]["balances"][0]["head_count"] == 12
    assert payload["active_grazing"][0]["allocations"][0]["allocation_fraction"] == 1.0
    assert payload["active_grazing_by_paddock"][0]["group_heads"][0]["animal_group_type"]["species"] == "Cattle"
    assert payload["active_grazing_by_paddock"][0]["group_heads"][0]["head"] == 12.0
    assert payload["active_grazing_by_paddock"][0]["mobs"][0]["start_at"].startswith(grazing_started_at.date().isoformat())
    assert payload["rainfall"][0]["mm"] == 14.0
    assert payload["mob_events"][0]["tags"] == ["health", "field note"]
    assert payload["paddock_events"][0]["tags"] == ["pasture", "field note"]
    assert payload["paddock_events"][0]["description"] == "Pasture recovering after rain"
    assert payload["water_asset_events"][0]["tags"] == ["inspection", "field note"]
    assert payload["water_asset_events"][0]["description"] == "Float valve checked on water run"
    assert payload["fence_sections"][0]["name"] == "North Camp Boundary Fence"
    assert payload["fence_sections"][0]["condition"] == "bad"
    assert payload["fence_sections"][0]["electric_wire"] is True
    assert payload["fence_events"][0]["event_type"] == "inspection"
    assert payload["fence_events"][0]["description"] == "Jackal hole under boundary fence"
    assert payload["incidents"][0]["category"] == "Stock missing"
    assert payload["incidents"][0]["note"] == "Two ewes missing from North Camp"
    assert payload["incidents"][0]["tags"] == ["stock", "security"]
    assert {asset["name"] for asset in payload["water_assets"]} == {"Header Tank", "North Trough"}
    assert payload["water_connections"][0]["flow_type"] == "gravity"
    assert payload["tasks"][0]["heading"] == "Check north trough"
    assert payload["tasks"][0]["tags"] == ["water", "field"]
    assert payload["tasks"][0]["assignee_name"] == "Field Team"
    assert payload["tasks"][0]["priority_label"] == "High"
    assert {link["entity_type"] for link in payload["tasks"][0]["entity_links"]} == {"paddock", "mob", "fence_section"}
    assert payload["calendar_items"][0]["title"] in {"Check north trough", "Weekly water run"}
    task_item = next(item for item in payload["calendar_items"] if item["kind"] == "task")
    assert task_item["source_id"] == task_id
    assert task_item["task_id"] == task_id
    assert task_item["description"] == "Confirm water level and float valve."
    assert task_item["stage"] == "todo"
    assert task_item["stage_label"] == "TO DO"
    assert task_item["assignee_name"] == "Field Team"
    assert task_item["tags"] == ["water", "field"]
    assert {link["entity_type"] for link in task_item["entity_links"]} == {"paddock", "mob", "fence_section"}
    activity_item = next(item for item in payload["calendar_items"] if item["kind"] == "activity")
    assert activity_item["source_id"] == activity_id
    assert activity_item["activity_id"] == activity_id
    assert activity_item["description"] == "Check tanks and troughs."
    assert activity_item["stage_label"] == "Activity"
    incident_item = next(item for item in payload["calendar_items"] if item["kind"] == "incident")
    assert incident_item["source_id"] == incident_id
    assert incident_item["incident_id"] == incident_id
    assert incident_item["description"] == "Two ewes missing from North Camp"
    assert incident_item["tags"] == ["stock", "security"]
    assert "decision_feed" in payload
    assert "map_features" in payload


def test_mobile_snapshot_hides_archived_mobs_and_related_mob_links(client, app):
    with app.app_context():
        farm = Farm(name="Archived Mobile Mob Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)

        paddock = Paddock(
            farm_id=farm.id,
            name="Archive Camp",
            area_ha=15,
            grazeable_area_ha=12,
        )
        active_mob = Mob(farm_id=farm.id, name="Active Mobile Mob", status="active")
        archived_mob = Mob(farm_id=farm.id, name="Archived Mobile Mob", status="archived")
        group = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add_all([paddock, active_mob, archived_mob, group])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(
                    mob_id=active_mob.id,
                    animal_group_type_id=group.id,
                    head_count=6,
                ),
                AnimalGroupBalance(
                    mob_id=archived_mob.id,
                    animal_group_type_id=group.id,
                    head_count=9,
                ),
            ]
        )
        stale_session = GrazingSession(
            farm_id=farm.id,
            mob_id=archived_mob.id,
            start_at=datetime.now(timezone.utc) - timedelta(days=14),
            end_at=None,
        )
        db.session.add(stale_session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=stale_session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )
        db.session.add(
            MobEvent(
                farm_id=farm.id,
                mob_id=archived_mob.id,
                tags_csv="archive",
                description="Archived mob note",
            )
        )
        space = TaskSpace(
            farm_id=farm.id,
            key="ARCHMOB",
            name="Archived Mob Tasks",
            description="Regression coverage",
        )
        db.session.add(space)
        db.session.flush()
        task = TaskService.create_task(
            space=space,
            heading="Inspect stock",
            description="Task should keep only active mob links on mobile.",
            raw_tags="field",
            reporter_name="Mobile User",
            assignee_name="Field Team",
            status="todo",
            priority="low",
            original_estimate_days=None,
            due_date=date.today().isoformat(),
        )
        db.session.flush()
        db.session.add_all(
            [
                TaskEntityLink(task_id=task.id, mob_id=active_mob.id),
                TaskEntityLink(task_id=task.id, mob_id=archived_mob.id),
            ]
        )

        farm_id = str(farm.id)
        active_mob_id = str(active_mob.id)
        archived_mob_id = str(archived_mob.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    payload = response.get_json()
    assert [mob["name"] for mob in payload["mobs"]] == ["Active Mobile Mob"]
    assert {mob["id"] for mob in payload["mobs"]} == {active_mob_id}
    assert all(session["mob_id"] != archived_mob_id for session in payload["active_grazing"])
    assert all(
        row_mob["mob_id"] != archived_mob_id
        for row in payload["active_grazing_by_paddock"]
        for row_mob in row["mobs"]
    )
    assert all(event["mob_id"] != archived_mob_id for event in payload["mob_events"])

    task_links = payload["tasks"][0]["entity_links"]
    calendar_task = next(item for item in payload["calendar_items"] if item["kind"] == "task")
    assert [link["entity_id"] for link in task_links if link["entity_type"] == "mob"] == [
        active_mob_id
    ]
    assert [
        link["entity_id"]
        for link in calendar_task["entity_links"]
        if link["entity_type"] == "mob"
    ] == [active_mob_id]


def test_mobile_decision_feed_ignores_weir_water_level(client, app):
    with app.app_context():
        farm = Farm(name="Weir Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.flush()
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today(), mm=4)
        )
        weir = WaterAsset(
            farm_id=farm.id,
            name="Crossing Weir",
            asset_type="weir",
            active=True,
            status="operational",
            water_level="empty",
        )
        db.session.add(weir)
        db.session.flush()
        db.session.add(WaterAssetServedPaddock(water_asset_id=weir.id, paddock_id=paddock.id))
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == []


def test_mobile_decision_feed_ignores_damaged_empty_water_asset(client, app):
    with app.app_context():
        farm = Farm(name="Damaged Empty Water Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today(), mm=4)
        )
        db.session.add(
            WaterAsset(
                farm_id=farm.id,
                name="Cracked Tank",
                asset_type="tank",
                active=True,
                status="damaged",
                water_level="empty",
            )
        )
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == []


def test_mobile_decision_feed_allows_rainfall_records_under_sixty_days(client, app):
    with app.app_context():
        farm = Farm(name="Recent Rain Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today() - timedelta(days=59), mm=2)
        )
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == []


def test_mobile_decision_feed_marks_rainfall_stale_after_sixty_days(client, app):
    with app.app_context():
        farm = Farm(name="Stale Rain Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today() - timedelta(days=60), mm=2)
        )
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == [
        {
            "severity": "medium",
            "category": "rainfall",
            "title": "Rainfall record is stale",
            "detail": f"Last rain was recorded on {(date.today() - timedelta(days=60)).isoformat()}.",
            "entity_type": "farm",
            "entity_id": farm_id,
        }
    ]


def test_mobile_decision_feed_flags_mobs_grazing_for_ten_days(client, app):
    with app.app_context():
        farm = Farm(name="Long Grazing Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        paddock = Paddock(farm_id=farm.id, name="Long Camp", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="South Mob", status="active")
        group = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        db.session.add_all([paddock, mob, group])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=18)
        )
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today(), mm=3)
        )
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime.combine(
                date.today() - timedelta(days=10),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
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
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == [
        {
            "severity": "high",
            "category": "grazing",
            "title": "Move South Mob off Long Camp",
            "detail": (
                "South Mob has been grazing Long Camp for 10 days continuously "
                "(limit 10 days, 100% allocation). Move the mob off this paddock."
            ),
            "entity_type": "mob",
            "entity_id": mob_id,
        }
    ]


def test_mobile_decision_feed_allows_mobs_grazing_under_ten_days(client, app):
    with app.app_context():
        farm = Farm(name="Current Grazing Decision Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        paddock = Paddock(farm_id=farm.id, name="Fresh Camp", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="North Mob", status="active")
        group = AnimalGroupType(species="Sheep", breed="Dorper", sex="ewe", age_class="adult")
        db.session.add_all([paddock, mob, group])
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=40)
        )
        db.session.add(
            RainfallRecord(farm_id=farm.id, recorded_on=date.today(), mm=3)
        )
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime.combine(
                date.today() - timedelta(days=9),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
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
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    response = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))

    assert response.status_code == 200
    assert response.get_json()["decision_feed"] == []


def test_mobile_task_attachment_upload_is_idempotent_and_visible_in_snapshot(client, app):
    with app.app_context():
        farm = Farm(name="Attachment Mobile Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        space = TaskSpace(
            farm_id=farm.id,
            key="PHOTO",
            name="Photo Tasks",
            description="Mobile photo task space",
        )
        db.session.add(space)
        db.session.flush()
        task = TaskService.create_task(
            space=space,
            heading="Photograph trough",
            description="Attach a current water photo.",
            raw_tags="water",
            reporter_name="Mobile User",
            assignee_name=None,
            status="todo",
            priority="high",
            original_estimate_days=None,
            due_date=None,
        )
        farm_id = str(farm.id)
        task_id = str(task.id)
        db.session.commit()

    token = _login(client)
    response = client.post(
        f"/api/mobile/v1/farms/{farm_id}/tasks/{task_id}/attachments",
        headers=_auth(token),
        data={
            "client_attachment_id": "photo-1",
            "caption": "North trough before repair",
            "file": (BytesIO(b"fake jpeg bytes"), "trough.jpg", "image/jpeg"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["duplicate"] is False
    attachment_id = payload["attachment"]["id"]

    duplicate = client.post(
        f"/api/mobile/v1/farms/{farm_id}/tasks/{task_id}/attachments",
        headers=_auth(token),
        data={
            "client_attachment_id": "photo-1",
            "caption": "Duplicate upload",
            "file": (BytesIO(b"other bytes"), "duplicate.jpg", "image/jpeg"),
        },
        content_type="multipart/form-data",
    )

    assert duplicate.status_code == 200
    assert duplicate.get_json()["duplicate"] is True
    assert duplicate.get_json()["attachment"]["id"] == attachment_id

    snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert snapshot.status_code == 200
    task_payload = snapshot.get_json()["tasks"][0]
    assert task_payload["attachment_count"] == 1
    assert task_payload["attachments"][0]["caption"] == "North trough before repair"

    file_response = client.get(
        f"/api/mobile/v1/farms/{farm_id}/tasks/{task_id}/attachments/{attachment_id}",
        headers=_auth(token),
    )
    assert file_response.status_code == 200
    assert file_response.data == b"fake jpeg bytes"

    with app.app_context():
        assert TaskAttachment.query.filter_by(task_id=task_id).count() == 1


def test_mobile_note_attachment_uploads_for_mob_paddock_and_water_notes(client, app, tmp_path):
    app.instance_path = str(tmp_path)
    with app.app_context():
        farm = Farm(name="Note Attachment Mobile Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        paddock = Paddock(farm_id=farm.id, name="Note Camp", area_ha=12, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Note Mob", status="active")
        asset = WaterAsset(
            farm_id=farm.id,
            name="Note Trough",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="full",
        )
        db.session.add_all([paddock, mob, asset])
        db.session.flush()
        fence = FenceSection(
            farm_id=farm.id,
            section_key="boundary:note-camp",
            name="Note Camp Fence",
            source=FenceSection.SOURCE_MANUAL,
            section_type=FenceSection.TYPE_BOUNDARY,
            paddock_a_id=paddock.id,
        )
        db.session.add(fence)
        db.session.flush()
        mob_event = MobEvent(
            farm_id=farm.id,
            mob_id=mob.id,
            tags_csv="health",
            description="Mob note with photo",
        )
        paddock_event = PaddockEvent(
            farm_id=farm.id,
            paddock_id=paddock.id,
            tags_csv="pasture",
            description="Paddock note with photo",
        )
        water_event = WaterAssetEvent(
            farm_id=farm.id,
            water_asset_id=asset.id,
            tags_csv="water",
            description="Water note with photo",
        )
        fence_event = FenceEvent(
            farm_id=farm.id,
            fence_section_id=fence.id,
            event_type="inspection",
            tags_csv="fence",
            description="Fence note with photo",
        )
        db.session.add_all([mob_event, paddock_event, water_event, fence_event])
        db.session.flush()
        farm_id = str(farm.id)
        event_targets = [
            ("mob", str(mob_event.id), f"/api/mobile/v1/farms/{farm.id}/mob-events/{mob_event.id}/attachments"),
            (
                "paddock",
                str(paddock_event.id),
                f"/api/mobile/v1/farms/{farm.id}/paddock-events/{paddock_event.id}/attachments",
            ),
            (
                "water",
                str(water_event.id),
                f"/api/mobile/v1/farms/{farm.id}/water-asset-events/{water_event.id}/attachments",
            ),
            (
                "fence",
                str(fence_event.id),
                f"/api/mobile/v1/farms/{farm.id}/fence-events/{fence_event.id}/attachments",
            ),
        ]
        db.session.commit()

    token = _login(client)
    uploaded = []
    for label, event_id, url in event_targets:
        response = client.post(
            url,
            headers=_auth(token),
            data={
                "client_attachment_id": f"{label}-photo-1",
                "caption": f"{label} note photo",
                "file": (BytesIO(f"{label} jpeg bytes".encode("utf-8")), f"{label}.jpg", "image/jpeg"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["duplicate"] is False
        assert payload["attachment"]["event_id"] == event_id
        uploaded.append(payload["attachment"])

    duplicate = client.post(
        event_targets[-1][2],
        headers=_auth(token),
        data={
            "client_attachment_id": "fence-photo-1",
            "file": (BytesIO(b"duplicate bytes"), "duplicate.jpg", "image/jpeg"),
        },
        content_type="multipart/form-data",
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["duplicate"] is True
    assert duplicate.get_json()["attachment"]["id"] == uploaded[-1]["id"]

    snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert snapshot.status_code == 200
    snapshot_payload = snapshot.get_json()
    assert snapshot_payload["mob_events"][0]["attachment_count"] == 1
    assert snapshot_payload["paddock_events"][0]["attachment_count"] == 1
    assert snapshot_payload["water_asset_events"][0]["attachment_count"] == 1
    assert snapshot_payload["fence_events"][0]["attachment_count"] == 1

    for attachment in uploaded:
        file_response = client.get(
            f"/api/mobile/v1/farms/{farm_id}/note-attachments/{attachment['id']}",
            headers=_auth(token),
        )
        assert file_response.status_code == 200
        assert file_response.data.endswith(b"jpeg bytes")

    with app.app_context():
        assert NoteAttachment.query.filter_by(farm_id=farm_id).count() == 4


def test_mobile_sync_commands_apply_and_duplicate_replay_is_idempotent(client, app):
    with app.app_context():
        farm = Farm(name="Sync Mobile Farm", timezone="SAST", active=True)
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
        transfer_target = Mob(farm_id=farm.id, name="Transfer Target", status="active")
        group = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        tank = WaterAsset(
            farm_id=farm.id,
            name="Sync Tank",
            asset_type="tank",
            active=True,
            status="operational",
            water_level="low",
        )
        db.session.add_all([source, destination, mob, transfer_target, group, tank])
        db.session.flush()
        fence_paddock_a_id, fence_paddock_b_id = sorted([str(source.id), str(destination.id)])
        fence = FenceSection(
            farm_id=farm.id,
            section_key="internal:sync-source-destination",
            name="Source / Destination Fence",
            source=FenceSection.SOURCE_MANUAL,
            section_type=FenceSection.TYPE_INTERNAL,
            paddock_a_id=fence_paddock_a_id,
            paddock_b_id=fence_paddock_b_id,
            condition="unknown",
        )
        db.session.add(fence)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=group.id, head_count=5)
        )
        space = TaskSpace(
            farm_id=farm.id,
            key="SYNC",
            name="Sync Tasks",
            description="Mobile sync task space",
        )
        db.session.add(space)
        db.session.flush()
        task = TaskService.create_task(
            space=space,
            heading="Repair gate",
            description="Gate is dragging.",
            raw_tags="field",
            reporter_name="Mobile User",
            assignee_name="Field Team",
            status="todo",
            priority="high",
            original_estimate_days=None,
            due_date=None,
        )
        farm_id = str(farm.id)
        destination_id = str(destination.id)
        mob_id = str(mob.id)
        transfer_target_id = str(transfer_target.id)
        group_id = str(group.id)
        tank_id = str(tank.id)
        fence_id = str(fence.id)
        task_id = str(task.id)
        source_id = str(source.id)
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
            "client_command_id": "mob-create-1",
            "type": "mob.create",
            "farm_id": farm_id,
            "payload": {"name": "Mobile Created Mob", "origin_note": "Created in the mobile app"},
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
            "client_command_id": "paddock-event-1",
            "type": "paddock_event.create",
            "farm_id": farm_id,
            "payload": {
                "paddock_id": source_id,
                "tags": ["pasture", "field"],
                "description": "Pasture cover noted from mobile",
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
                    {"paddock_id": destination_id, "allocation_fraction": 0.5},
                    {"paddock_id": source_id, "allocation_fraction": 0.5},
                ],
            },
        },
        {
            "client_command_id": "transfer-1",
            "type": "mob.transfer",
            "farm_id": farm_id,
            "payload": {
                "source_mob_id": mob_id,
                "destination_mob_id": transfer_target_id,
                "transfers": [{"animal_group_type_id": group_id, "quantity": 2}],
                "note": "Mobile transfer",
            },
        },
        {
            "client_command_id": "task-create-1",
            "type": "task.create",
            "farm_id": farm_id,
            "payload": {
                "heading": "Mobile-created task",
                "description": "Created while offline",
                "paddock_id": source_id,
                "fence_section_id": fence_id,
                "due_date": "2026-05-18",
            },
        },
        {
            "client_command_id": "task-status-1",
            "type": "task.status.update",
            "farm_id": farm_id,
            "payload": {"task_id": task_id, "status": "in_progress", "note": "Started in field"},
        },
        {
            "client_command_id": "task-comment-1",
            "type": "task.comment.create",
            "farm_id": farm_id,
            "payload": {"task_id": task_id, "body": "Photo checked on phone"},
        },
        {
            "client_command_id": "paddock-1",
            "type": "paddock.update",
            "farm_id": farm_id,
            "payload": {
                "paddock_id": source_id,
                "status": "resting",
                "notes": "Gate latch needs attention",
                "tags": ["gate", "field"],
            },
        },
        {
            "client_command_id": "water-1",
            "type": "water_asset_status.update",
            "farm_id": farm_id,
            "payload": {"water_asset_id": tank_id, "water_level": "full"},
        },
        {
            "client_command_id": "water-note-1",
            "type": "water_asset_event.create",
            "farm_id": farm_id,
            "payload": {
                "water_asset_id": tank_id,
                "tags": ["inspection", "field"],
                "description": "Tank inspected from mobile",
            },
        },
        {
            "client_command_id": "fence-1",
            "type": "fence_section.update",
            "farm_id": farm_id,
            "payload": {
                "fence_section_id": fence_id,
                "condition": "bad",
                "electric_wire": True,
                "notes": "Loose bottom wire near the drift",
            },
        },
        {
            "client_command_id": "fence-note-1",
            "type": "fence_event.create",
            "farm_id": farm_id,
            "payload": {
                "fence_section_id": fence_id,
                "event_type": "maintenance",
                "tags": ["fence", "jackal"],
                "condition_after": "fair",
                "description": "Packed stones under the low section",
                "materials": [
                    {
                        "action": "packed",
                        "material_type": "stone",
                        "quantity": "2",
                        "unit": "bag",
                    }
                ],
            },
        },
        {
            "client_command_id": "incident-1",
            "type": "incident.create",
            "farm_id": farm_id,
            "payload": {
                "occurred_on": "2026-05-18",
                "category": "Stock missing",
                "tags": ["stock", "security"],
                "note": "Two ewes missing from North Camp",
            },
        },
    ]
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": commands},
        headers=_auth(token),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert [result["status"] for result in payload["results"]] == ["applied"] * 16
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
        created_mob = Mob.query.filter_by(farm_id=farm_id, name="Mobile Created Mob").one()
        assert created_mob.origin_note == "Created in the mobile app"
        mob_events = MobEvent.query.filter_by(farm_id=farm_id).all()
        assert len(mob_events) == 3
        mob_event_descriptions = [event.description for event in mob_events]
        assert any("Mob looks settled" in description for description in mob_event_descriptions)
        assert any(
            "Stock transfer out to Transfer Target" in description
            and "Mobile transfer" in description
            for description in mob_event_descriptions
        )
        assert any(
            "Stock transfer in from Sync Mob" in description
            and "Mobile transfer" in description
            for description in mob_event_descriptions
        )
        assert PaddockEvent.query.filter_by(farm_id=farm_id).count() == 1
        assert WaterAssetEvent.query.filter_by(farm_id=farm_id).count() == 1
        assert FenceEvent.query.filter_by(farm_id=farm_id).count() == 1
        assert Incident.query.filter_by(farm_id=farm_id).count() == 1
        assert Incident.query.filter_by(farm_id=farm_id).one().category == "Stock missing"
        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=group_id,
        ).first()
        assert balance.head_count == 5
        target_balance = AnimalGroupBalance.query.filter_by(
            mob_id=transfer_target_id,
            animal_group_type_id=group_id,
        ).first()
        assert target_balance.head_count == 2
        active_session = GrazingSession.query.filter_by(farm_id=farm_id, mob_id=mob_id, end_at=None).first()
        assert active_session is not None
        assert sorted(float(row.allocation_fraction) for row in active_session.allocations) == [0.4, 0.6]
        assert sorted(
            assignment.head_count
            for allocation in active_session.allocations
            for assignment in allocation.group_assignments
        ) == [2, 3]
        assert db.session.get(WaterAsset, tank_id).water_level == "full"
        assert db.session.get(FenceSection, fence_id).condition == "fair"
        assert db.session.get(FenceSection, fence_id).electric_wire is True
        water_history = WaterAssetStateHistory.query.filter_by(water_asset_id=tank_id).one()
        assert water_history.change_type == "updated"
        assert water_history.previous_water_level == "low"
        assert water_history.water_level == "full"
        assert db.session.get(Paddock, source_id).status == "resting"
        assert db.session.get(Paddock, source_id).notes == "Gate latch needs attention"
        assert Task.query.filter_by(heading="Mobile-created task").count() == 1
        created_task = Task.query.filter_by(heading="Mobile-created task").one()
        assert any(str(link.fence_section_id) == fence_id for link in created_task.entity_links)
        assert db.session.get(Task, task_id).status == "in_progress"
        assert TaskComment.query.filter_by(task_id=task_id).count() == 1
        assert MobileSyncCommand.query.filter_by(status="applied").count() == 16

    snapshot_response = client.get(
        f"/api/mobile/v1/farms/{farm_id}/snapshot",
        headers=_auth(token),
    )
    assert snapshot_response.status_code == 200
    snapshot_payload = snapshot_response.get_json()
    history_payload = snapshot_payload["water_asset_state_history"]
    assert len(history_payload) == 1
    assert history_payload[0]["water_asset_id"] == tank_id
    assert history_payload[0]["previous_water_level"] == "low"
    assert history_payload[0]["water_level"] == "full"
    assert snapshot_payload["fence_sections"][0]["condition"] == "fair"
    assert snapshot_payload["fence_events"][0]["materials"][0]["material_type"] == "stone"
    assert snapshot_payload["incidents"][0]["category"] == "Stock missing"


def test_mobile_mob_move_supports_count_allocations(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Count Move Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        north = Paddock(farm_id=farm.id, name="North Counts", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Counts", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Mobile Count Mob", status="active")
        cattle = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([north, south, mob, cattle, sheep])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=cattle.id, head_count=5),
                AnimalGroupBalance(mob_id=mob.id, animal_group_type_id=sheep.id, head_count=12),
            ]
        )
        db.session.commit()
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        north_id = str(north.id)
        south_id = str(south.id)
        cattle_id = str(cattle.id)
        sheep_id = str(sheep.id)

    token = _login(client)
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "count-move-1",
                    "type": "mob.move",
                    "farm_id": farm_id,
                    "payload": {
                        "mob_id": mob_id,
                        "allocation_mode": "counts",
                        "allocations": [
                            {
                                "paddock_id": north_id,
                                "group_counts": [
                                    {"animal_group_type_id": sheep_id, "head_count": 5}
                                ],
                            },
                            {
                                "paddock_id": north_id,
                                "group_counts": [
                                    {"animal_group_type_id": sheep_id, "head_count": 7}
                                ],
                            },
                            {
                                "paddock_id": south_id,
                                "group_counts": [
                                    {"animal_group_type_id": cattle_id, "head_count": 5}
                                ],
                            },
                        ],
                    },
                }
            ]
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.get_json()["results"][0]["status"] == "applied"

    with app.app_context():
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {str(row.paddock_id): float(row.allocation_fraction) for row in session.allocations}
        assert allocations == {north_id: 0.2857, south_id: 0.7143}
        assert GrazingAllocationGroupAssignment.query.count() == 2

    snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert snapshot.status_code == 200
    payload = snapshot.get_json()
    active_allocations = payload["active_grazing"][0]["allocations"]
    assert any(row.get("group_counts") for row in active_allocations)
    by_paddock = {row["paddock_id"]: row for row in payload["active_grazing_by_paddock"]}
    assert by_paddock[north_id]["group_heads"][0]["head"] == 12.0
    assert by_paddock[south_id]["group_heads"][0]["head"] == 5.0


def test_mobile_snapshot_includes_gates_and_gate_update_command(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Gate Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        north = Paddock(farm_id=farm.id, name="North", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South", area_ha=30, grazeable_area_ha=30)
        mob = Mob(farm_id=farm.id, name="Gate Mob", status="active")
        db.session.add_all([north, south, mob])
        db.session.flush()
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": north.id, "allocation_fraction": "1.0"}],
            destination_farm_id=str(farm.id),
            when=datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc),
        )
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
            latitude="-32.00000000",
            longitude="25.00000000",
        )
        db.session.commit()
        farm_id = str(farm.id)
        gate_id = str(gate.id)
        north_id = str(north.id)
        south_id = str(south.id)
        mob_id = str(mob.id)

    token = _login(client)
    snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert snapshot.status_code == 200
    snapshot_payload = snapshot.get_json()
    assert snapshot_payload["gates"][0]["id"] == gate_id
    assert snapshot_payload["gates"][0]["status"] == "closed"
    gate_features = [
        feature for feature in snapshot_payload["map_features"]
        if feature["properties"].get("feature_type") == "gate"
    ]
    assert len(gate_features) == 1
    assert gate_features[0]["properties"]["gate_id"] == gate_id

    command = {
        "client_command_id": "gate-open-1",
        "type": "gate.update",
        "farm_id": farm_id,
        "payload": {
            "gate_id": gate_id,
            "status": "open",
            "event_time": "2026-06-12T10:00:00+00:00",
            "closure_choices": [],
        },
    }
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": [command]},
        headers=_auth(token),
    )
    assert response.status_code == 200
    result = response.get_json()["results"][0]
    assert result["status"] == "applied"
    assert result["response"]["gate"]["status"] == "open"
    assert result["response"]["moved_mob_count"] == 1

    duplicate = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": [command]},
        headers=_auth(token),
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["results"][0]["duplicate"] is True

    with app.app_context():
        assert db.session.get(PaddockGate, gate_id).status == "open"
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {str(row.paddock_id): float(row.allocation_fraction) for row in session.allocations}
        assert allocations == {north_id: 0.25, south_id: 0.75}

    refreshed = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert refreshed.status_code == 200
    assert refreshed.get_json()["gates"][0]["status"] == "open"


def test_mobile_mob_move_into_open_gate_uses_gate_allocations(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Open Gate Move Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        north = Paddock(farm_id=farm.id, name="North Open", area_ha=10, grazeable_area_ha=10)
        south = Paddock(farm_id=farm.id, name="South Open", area_ha=30, grazeable_area_ha=30)
        mob = Mob(farm_id=farm.id, name="Open Gate Move Mob", status="active")
        db.session.add_all([north, south, mob])
        db.session.flush()
        gate = GateService.create_manual_gate(
            farm_id=str(farm.id),
            paddock_a_id=str(north.id),
            paddock_b_id=str(south.id),
        )
        gate.status = "open"
        db.session.commit()
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        north_id = str(north.id)
        south_id = str(south.id)

    token = _login(client)
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "move-into-open-gate-1",
                    "type": "mob.move",
                    "farm_id": farm_id,
                    "payload": {
                        "mob_id": mob_id,
                        "allocations": [
                            {"paddock_id": south_id, "allocation_fraction": 1.0},
                        ],
                    },
                }
            ]
        },
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.get_json()["results"][0]["status"] == "applied"

    with app.app_context():
        session = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).one()
        allocations = {str(row.paddock_id): float(row.allocation_fraction) for row in session.allocations}
        assert allocations == {north_id: 0.25, south_id: 0.75}


def test_mobile_stock_count_can_create_animal_group_from_payload(client, app):
    with app.app_context():
        farm = Farm(name="New Group Mobile Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        mob = Mob(farm_id=farm.id, name="New Group Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        db.session.commit()

    token = _login(client)
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "new-group-count-1",
                    "type": "stock_count.record",
                    "farm_id": farm_id,
                    "payload": {
                        "mob_id": mob_id,
                        "animal_group_type": {
                            "species": "Sheep",
                            "breed": "Merino",
                            "sex": "ewe",
                            "age_class": "adult",
                        },
                        "quantity": 17,
                        "note": "Mobile new group count",
                    },
                }
            ]
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    result = response.get_json()["results"][0]
    assert result["status"] == "applied"
    group_id = result["response"]["animal_group_type_id"]

    with app.app_context():
        group = AnimalGroupType.query.filter_by(
            species="Sheep",
            breed="Merino",
            sex="ewe",
            age_class="adult",
        ).one()
        assert str(group.id) == group_id
        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=group.id,
        ).one()
        assert balance.head_count == 17
        ledger = StockLedgerEntry.query.filter_by(
            mob_id=mob_id,
            animal_group_type_id=group.id,
        ).one()
        assert ledger.event_type.value == "adjustment_in"
        assert ledger.quantity == 17


def test_mobile_snapshot_and_stock_count_keep_same_type_cohorts_distinct(client, app):
    with app.app_context():
        farm = Farm(name="Cohort Mobile Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        mob = Mob(farm_id=farm.id, name="Cohort Mob", status="active")
        destination_mob = Mob(farm_id=farm.id, name="Cohort Destination", status="active")
        group = AnimalGroupType(
            species="Sheep", breed="Merino", sex="ewe", age_class="adult"
        )
        db.session.add_all([mob, destination_mob, group])
        db.session.flush()
        pregnant = AnimalCohort(
            animal_group_type_id=group.id,
            origin_farm_id=farm.id,
            origin="manual",
            reproductive_state="pregnant",
            expected_litter_size="twins",
            lactation_state="dry",
            offspring_at_foot="none",
        )
        parturated = AnimalCohort(
            animal_group_type_id=group.id,
            origin_farm_id=farm.id,
            origin="manual",
            reproductive_state="parturated",
            lactation_state="lactating",
            offspring_at_foot="single",
        )
        db.session.add_all([pregnant, parturated])
        db.session.flush()
        db.session.add_all(
            [
                AnimalGroupBalance(
                    mob_id=mob.id,
                    animal_group_type_id=group.id,
                    cohort_id=pregnant.id,
                    head_count=8,
                ),
                AnimalGroupBalance(
                    mob_id=mob.id,
                    animal_group_type_id=group.id,
                    cohort_id=parturated.id,
                    head_count=5,
                ),
            ]
        )
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        destination_mob_id = str(destination_mob.id)
        pregnant_id = str(pregnant.id)
        parturated_id = str(parturated.id)
        group_id = str(group.id)
        db.session.commit()

    token = _login(client)
    snapshot = client.get(
        f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token)
    ).get_json()
    source_snapshot = next(row for row in snapshot["mobs"] if row["id"] == mob_id)
    balances = {row["cohort_id"]: row for row in source_snapshot["balances"]}
    assert balances[pregnant_id]["reproductive_state"] == "pregnant"
    assert balances[pregnant_id]["expected_litter_size"] == "twins"
    assert balances[pregnant_id]["lactation_state"] == "dry"
    assert balances[parturated_id]["offspring_at_foot"] == "single"

    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "cohort-count-1",
                    "type": "stock_count.record",
                    "farm_id": farm_id,
                    "payload": {
                        "mob_id": mob_id,
                        "animal_group_type_id": group_id,
                        "cohort_id": pregnant_id,
                        "quantity": 7,
                    },
                }
            ]
        },
        headers=_auth(token),
    )
    assert response.get_json()["results"][0]["status"] == "applied"

    with app.app_context():
        assert AnimalGroupBalance.query.filter_by(cohort_id=pregnant_id).one().head_count == 7
        assert AnimalGroupBalance.query.filter_by(cohort_id=parturated_id).one().head_count == 5

    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "cohort-transfer-1",
                    "type": "mob.transfer",
                    "farm_id": farm_id,
                    "payload": {
                        "source_mob_id": mob_id,
                        "destination_mob_id": destination_mob_id,
                        "transfers": [
                            {
                                "animal_group_type_id": group_id,
                                "cohort_id": pregnant_id,
                                "quantity": 2,
                            }
                        ],
                    },
                }
            ]
        },
        headers=_auth(token),
    )
    assert response.get_json()["results"][0]["status"] == "applied"

    with app.app_context():
        assert AnimalGroupBalance.query.filter_by(cohort_id=pregnant_id).one().head_count == 5
        assert AnimalGroupBalance.query.filter_by(cohort_id=parturated_id).one().head_count == 5
        moved = AnimalGroupBalance.query.filter_by(mob_id=destination_mob_id).one()
        assert moved.head_count == 2
        assert moved.cohort.reproductive_state == "pregnant"
        assert moved.cohort.expected_litter_size == "twins"


def test_mobile_stock_count_new_group_validation_failures(client, app):
    with app.app_context():
        farm = Farm(name="Invalid New Group Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        mob = Mob(farm_id=farm.id, name="Invalid Group Mob", status="active")
        db.session.add(mob)
        db.session.flush()
        farm_id = str(farm.id)
        mob_id = str(mob.id)
        db.session.commit()

    def command(client_id, group_payload, quantity=5):
        return {
            "client_command_id": client_id,
            "type": "stock_count.record",
            "farm_id": farm_id,
            "payload": {
                "mob_id": mob_id,
                "animal_group_type": group_payload,
                "quantity": quantity,
            },
        }

    token = _login(client)
    valid_group = {"species": "Sheep", "breed": "Merino", "sex": "ewe", "age_class": "adult"}
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                command("bad-species", {**valid_group, "species": "Horse"}),
                command("bad-sex", {**valid_group, "sex": "cow"}),
                command("bad-age", {**valid_group, "age_class": "calf"}),
                command("blank-breed", {**valid_group, "breed": "  "}),
                command("negative-count", valid_group, -1),
                command("zero-new-count", valid_group, 0),
            ]
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    results = response.get_json()["results"]
    assert [result["status"] for result in results] == ["failed"] * 6
    assert {result["error"]["code"] for result in results} == {"invalid_command"}

    with app.app_context():
        assert AnimalGroupBalance.query.filter_by(mob_id=mob_id).count() == 0
        assert StockLedgerEntry.query.filter_by(mob_id=mob_id).count() == 0


def test_mobile_task_close_requires_note(client, app):
    with app.app_context():
        farm = Farm(name="Close Note Mobile Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_user("mobile@example.com", "correct-password", farm)
        space = TaskSpace(
            farm_id=farm.id,
            key="CLOSE",
            name="Close Note Tasks",
            description="Mobile close note tasks",
        )
        db.session.add(space)
        db.session.flush()
        task = TaskService.create_task(
            space=space,
            heading="Close with note",
            description="Must capture the close reason.",
            raw_tags="",
            reporter_name="Mobile User",
            assignee_name=None,
            status="todo",
            priority="high",
            original_estimate_days=None,
            due_date=None,
        )
        farm_id = str(farm.id)
        task_id = str(task.id)
        db.session.commit()

    token = _login(client)
    missing_note = client.post(
        "/api/mobile/v1/sync/commands",
        headers=_auth(token),
        json={
            "commands": [
                {
                    "client_command_id": "close-without-note",
                    "type": "task.status.update",
                    "farm_id": farm_id,
                    "payload": {"task_id": task_id, "status": "closed"},
                }
            ]
        },
    )

    assert missing_note.status_code == 200
    missing_result = missing_note.get_json()["results"][0]
    assert missing_result["status"] == "failed"
    assert missing_result["error"]["message"] == "Closing a task requires a note"

    with app.app_context():
        assert db.session.get(Task, task_id).status == "todo"

    with_note = client.post(
        "/api/mobile/v1/sync/commands",
        headers=_auth(token),
        json={
            "commands": [
                {
                    "client_command_id": "close-with-note",
                    "type": "task.status.update",
                    "farm_id": farm_id,
                    "payload": {"task_id": task_id, "status": "closed", "note": "Water point repaired."},
                }
            ]
        },
    )

    assert with_note.status_code == 200
    assert with_note.get_json()["results"][0]["status"] == "applied"
    with app.app_context():
        assert db.session.get(Task, task_id).status == "closed"
        transition = TaskStatusTransition.query.filter_by(task_id=task_id, to_status="closed").one()
        assert transition.note == "Water point repaired."


def test_mobile_sync_commands_report_partial_failures_and_missing_records(client, app):
    with app.app_context():
        farm = Farm(name="Partial Sync Farm", timezone="SAST", active=True)
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
