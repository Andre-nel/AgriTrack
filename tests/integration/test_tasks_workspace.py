from datetime import date, timedelta

from app.extensions import db
from app.models import Farm, Mob, Paddock, Task, TaskComment, TaskEntityLink, TaskLink, TaskSpace, TaskSpaceComment, WaterAsset
from app.services.task_service import TaskService


def _create_farm(name: str) -> Farm:
    farm = Farm(name=name, timezone="UTC")
    db.session.add(farm)
    db.session.flush()
    return farm


def _create_space(farm: Farm, key: str, name: str) -> TaskSpace:
    space = TaskSpace(farm_id=farm.id, key=key, name=name, description=f"{name} description")
    db.session.add(space)
    db.session.flush()
    return space


def _create_task(space: TaskSpace, heading: str, *, status: str = "todo", priority: str = "low", due_date=None) -> Task:
    task = TaskService.create_task(
        space=space,
        heading=heading,
        description=f"{heading} description",
        raw_tags="operations,planning",
        reporter_name="Reporter",
        assignee_name="Assignee",
        status=status,
        priority=priority,
        original_estimate_days="2",
        due_date=due_date.isoformat() if due_date else "",
    )
    db.session.flush()
    return task


def test_dashboard_renders_tasks_nav(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/tasks"' in response.data
    assert b">Tasks<" in response.data


def test_tasks_landing_can_create_space(client, app):
    with app.app_context():
        farm = _create_farm("Task Farm")
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get("/tasks")
    assert response.status_code == 200
    assert b"Tasks Workspace" in response.data

    response = client.post(
        "/tasks/spaces",
        data={
            "farm_id": farm_id,
            "key": "ops",
            "name": "Operations",
            "description": "Operational work for the current season.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Operations" in body
    assert "OPS" in body

    with app.app_context():
        space = TaskSpace.query.filter_by(key="OPS").first()
        assert space is not None
        assert space.name == "Operations"


def test_space_page_can_create_task_and_show_board(client, app):
    with app.app_context():
        farm = _create_farm("Board Farm")
        space = _create_space(farm, "BRD", "Board")
        db.session.commit()
        space_id = str(space.id)

    response = client.post(
        f"/tasks/spaces/{space_id}/tasks",
        data={
            "heading": "Install new fence strainers",
            "description": "Replace the broken strainers on the west boundary fence.",
            "tags": "fencing, boundary",
            "reporter_name": "Ava",
            "assignee_name": "Noah",
            "status": "in_progress",
            "priority": "high",
            "original_estimate_days": "1.5",
            "due_date": "2026-03-20",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Install new fence strainers" in body
    assert "In Progress" in body
    assert "boundary" in body
    assert 'class="task-card-summary"' in body
    assert "Open Task" in body

    with app.app_context():
        task = Task.query.join(TaskSpace).filter(TaskSpace.key == "BRD").first()
        assert task is not None
        assert task.started_at is not None
        assert task.display_key == "BRD-1"


def test_task_creation_and_detail_manage_farm_entity_links(client, app):
    with app.app_context():
        farm = _create_farm("Entity Link Farm")
        space = _create_space(farm, "ENT", "Entity Links")
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=20, grazeable_area_ha=18)
        second_paddock = Paddock(farm_id=farm.id, name="South Camp", area_ha=15, grazeable_area_ha=12)
        tank = WaterAsset(farm_id=farm.id, name="Header Tank", asset_type="tank", active=True)
        mob = Mob(farm_id=farm.id, name="Main Mob", status="active")
        db.session.add_all([paddock, second_paddock, tank, mob])
        db.session.commit()
        space_id = str(space.id)
        paddock_id = str(paddock.id)
        second_paddock_id = str(second_paddock.id)
        tank_id = str(tank.id)
        mob_id = str(mob.id)

    response = client.post(
        f"/tasks/spaces/{space_id}/tasks",
        data={
            "heading": "Inspect water and fences",
            "description": "Check the paddock fence and water storage.",
            "tags": "field",
            "reporter_name": "Ava",
            "assignee_name": "Noah",
            "status": "todo",
            "priority": "high",
            "original_estimate_days": "",
            "due_date": "2026-06-01",
            "paddock_ids": [paddock_id],
            "water_asset_ids": [tank_id],
            "mob_ids": [mob_id],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        task = Task.query.filter_by(heading="Inspect water and fences").first()
        assert task is not None
        task_id = str(task.id)
        assert TaskEntityLink.query.filter_by(task_id=task_id).count() == 3
        paddock_link_id = str(TaskEntityLink.query.filter_by(task_id=task_id, paddock_id=paddock_id).first().id)

    detail = client.get(f"/tasks/{task_id}")
    body = detail.data.decode("utf-8")
    assert "North Camp" in body
    assert "Header Tank" in body
    assert "Main Mob" in body

    duplicate = client.post(
        f"/tasks/{task_id}/entity-links",
        data={"paddock_ids": [paddock_id], "water_asset_ids": [], "mob_ids": []},
        follow_redirects=True,
    )
    assert duplicate.status_code == 200
    assert b"No new farm entity links were added" in duplicate.data

    with app.app_context():
        assert TaskEntityLink.query.filter_by(task_id=task_id).count() == 3

    add_second = client.post(
        f"/tasks/{task_id}/entity-links",
        data={"paddock_ids": [second_paddock_id]},
        follow_redirects=True,
    )
    assert add_second.status_code == 200
    assert b"South Camp" in add_second.data

    delete_response = client.post(
        f"/tasks/{task_id}/entity-links/{paddock_link_id}/delete",
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert b"Farm entity link removed" in delete_response.data

    with app.app_context():
        assert TaskEntityLink.query.filter_by(task_id=task_id, paddock_id=paddock_id).count() == 0
        assert TaskEntityLink.query.filter_by(task_id=task_id).count() == 3


def test_new_task_page_prefills_entity_links_and_rejects_cross_farm_links(client, app):
    with app.app_context():
        farm = _create_farm("Prefill Farm")
        other_farm = _create_farm("Other Entity Farm")
        space = _create_space(farm, "PRE", "Prefill")
        paddock = Paddock(farm_id=farm.id, name="Prefilled Camp", area_ha=10, grazeable_area_ha=9)
        other_paddock = Paddock(farm_id=other_farm.id, name="Wrong Farm Camp", area_ha=8, grazeable_area_ha=7)
        db.session.add_all([paddock, other_paddock])
        db.session.commit()
        farm_id = str(farm.id)
        space_id = str(space.id)
        paddock_id = str(paddock.id)
        other_paddock_id = str(other_paddock.id)

    prefill = client.get(f"/tasks/new?paddock_id={paddock_id}")
    assert prefill.status_code == 200
    prefill_body = prefill.data.decode("utf-8")
    assert "Prefilled Camp" in prefill_body
    assert f'value="{paddock_id}" selected' in prefill_body

    create_response = client.post(
        "/tasks/new",
        data={
            "farm_id": farm_id,
            "space_id": space_id,
            "heading": "Check prefilled camp",
            "description": "Created from the paddock view.",
            "reporter_name": "Ava",
            "assignee_name": "",
            "status": "todo",
            "priority": "low",
            "original_estimate_days": "",
            "due_date": "",
            "tags": "",
            "paddock_ids": [paddock_id],
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    assert b"Prefilled Camp" in create_response.data

    with app.app_context():
        created_task = Task.query.filter_by(heading="Check prefilled camp").first()
        assert created_task is not None
        assert TaskEntityLink.query.filter_by(task_id=created_task.id, paddock_id=paddock_id).count() == 1

    cross_farm = client.post(
        "/tasks/new",
        data={
            "farm_id": farm_id,
            "space_id": space_id,
            "heading": "Bad link",
            "description": "This should fail.",
            "reporter_name": "Ava",
            "assignee_name": "",
            "status": "todo",
            "priority": "low",
            "original_estimate_days": "",
            "due_date": "",
            "tags": "",
            "paddock_ids": [other_paddock_id],
        },
        follow_redirects=True,
    )
    assert cross_farm.status_code == 200
    assert b"Selected paddocks must belong to the task farm" in cross_farm.data


def test_farm_entity_views_render_linked_tasks_and_create_task_actions(client, app):
    with app.app_context():
        farm = _create_farm("Entity View Farm")
        space = _create_space(farm, "VIEW", "Entity Views")
        paddock = Paddock(farm_id=farm.id, name="Task Camp", area_ha=12, grazeable_area_ha=10)
        asset = WaterAsset(farm_id=farm.id, name="Task Trough", asset_type="trough", active=True)
        mob = Mob(farm_id=farm.id, name="Task Mob", status="active")
        db.session.add_all([paddock, asset, mob])
        db.session.flush()
        task = _create_task(space, "Linked from entity", status="todo")
        TaskService.add_entity_links(
            task=task,
            paddock_ids=[str(paddock.id)],
            water_asset_ids=[str(asset.id)],
            mob_ids=[str(mob.id)],
        )
        db.session.commit()
        farm_id = str(farm.id)
        paddock_id = str(paddock.id)
        asset_id = str(asset.id)
        mob_id = str(mob.id)
        task_key = task.display_key

    paddock_page = client.get(f"/paddocks/{paddock_id}")
    assert paddock_page.status_code == 200
    assert f"/tasks/new?paddock_id={paddock_id}".encode("utf-8") in paddock_page.data
    assert task_key.encode("utf-8") in paddock_page.data
    assert b"Linked from entity" in paddock_page.data

    mob_page = client.get(f"/mobs/{mob_id}")
    assert mob_page.status_code == 200
    assert f"/tasks/new?mob_id={mob_id}".encode("utf-8") in mob_page.data
    assert task_key.encode("utf-8") in mob_page.data

    water_page = client.get(f"/farms/{farm_id}/water")
    assert water_page.status_code == 200
    assert f"/tasks/new?water_asset_id={asset_id}".encode("utf-8") in water_page.data
    assert task_key.encode("utf-8") in water_page.data


def test_task_status_transitions_track_start_close_and_reopen(client, app):
    with app.app_context():
        farm = _create_farm("Workflow Farm")
        space = _create_space(farm, "WRK", "Workflow")
        task = _create_task(space, "Move pump", status="todo")
        db.session.commit()
        task_id = str(task.id)

    response = client.post(
        f"/tasks/{task_id}/status",
        data={"status": "selected_for_execution", "changed_by_name": "Mia", "note": "Ready for field crew."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Selected For Execution" in response.data

    with app.app_context():
        task = Task.query.filter_by(id=task_id).first()
        started_at = task.started_at
        assert started_at is not None

    response = client.post(
        f"/tasks/{task_id}/status",
        data={"status": "closed", "changed_by_name": "Mia", "note": "Installed and tested."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Closed" in response.data

    with app.app_context():
        task = Task.query.filter_by(id=task_id).first()
        assert task.closed_at is not None
        assert task.started_at == started_at

    response = client.post(
        f"/tasks/{task_id}/status",
        data={"status": "todo", "changed_by_name": "Mia", "note": "Reopened after leak found."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"TO DO" in response.data

    with app.app_context():
        task = Task.query.filter_by(id=task_id).first()
        assert task.closed_at is None
        assert task.started_at == started_at


def test_task_workspace_supports_comments_links_and_counts(client, app):
    overdue_date = date.today() - timedelta(days=1)
    with app.app_context():
        farm = _create_farm("Coordination Farm")
        source_space = _create_space(farm, "OPS", "Operations")
        target_space = _create_space(farm, "PLN", "Planning")
        source_task = _create_task(
            source_space,
            "Order fence wire",
            status="impeded",
            priority="highest",
            due_date=overdue_date,
        )
        _create_task(source_space, "Review trough checklist", status="todo")
        _create_task(source_space, "Load new posts", status="selected_for_execution")
        _create_task(source_space, "String hotwire", status="in_progress")
        _create_task(source_space, "Verify gate repair", status="ready_for_verification")
        closed_task = _create_task(source_space, "Archive supplier follow-up", status="selected_for_execution")
        TaskService.apply_status_transition(closed_task, "closed", "Reporter", "Archived supplier follow-up.")
        target_task = _create_task(target_space, "Approve budget", status="todo")
        db.session.commit()
        source_space_id = str(source_space.id)
        source_task_id = str(source_task.id)
        target_space_id = str(target_space.id)
        target_task_key = target_task.display_key

    response = client.post(
        f"/tasks/spaces/{source_space_id}/comments",
        data={"author_name": "Ava", "body": "Waiting on supplier confirmation."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Waiting on supplier confirmation." in response.data
    assert b"Overdue" in response.data
    response_body = response.data.decode("utf-8")
    assert "<span>Tasks</span><strong>6</strong>" in response_body
    assert "<span>TO DO</span><strong>1</strong>" in response_body
    assert "<span>Open</span><strong>3</strong>" in response_body
    assert "<span>Impeded</span><strong>1</strong>" in response_body
    assert "<span>Closed</span><strong>1</strong>" in response_body
    assert "<span>Overdue</span><strong>1</strong>" in response_body

    response = client.post(
        f"/tasks/{source_task_id}/comments",
        data={"author_name": "Noah", "body": "Supplier asked for a revised quantity."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Supplier asked for a revised quantity." in response.data

    response = client.post(
        f"/tasks/spaces/{source_space_id}/links",
        data={"link_type": "references", "target_space_id": target_space_id, "target_task_key": "", "note": "Budget planning lives here."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Planning" in response.data

    response = client.post(
        f"/tasks/{source_task_id}/links",
        data={"link_type": "blocks", "target_space_id": "", "target_task_key": target_task_key, "note": "Cannot order until budget is approved."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert target_task_key.encode("utf-8") in response.data

    response = client.post(
        f"/tasks/{source_task_id}/links",
        data={"link_type": "relates_to", "target_space_id": target_space_id, "target_task_key": "", "note": "Related planning board."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Related planning board." in response.data

    with app.app_context():
        assert TaskSpaceComment.query.count() == 1
        assert TaskComment.query.count() == 1
        assert TaskLink.query.count() == 3

    landing = client.get("/tasks")
    assert landing.status_code == 200
    landing_body = landing.data.decode("utf-8")
    assert "<h2>TO DO</h2><p>2</p>" in landing_body
    assert "<h2>Open</h2><p>3</p>" in landing_body
    assert "<h2>Impeded</h2><p>1</p>" in landing_body
    assert "<h2>Closed</h2><p>1</p>" in landing_body
    assert "<h2>Overdue</h2><p>1</p>" in landing_body
    assert "<strong>1</strong><span>TO DO</span>" in landing_body
    assert "<strong>3</strong><span>Open</span>" in landing_body
    assert "<strong>1</strong><span>Impeded</span>" in landing_body
    assert "<strong>1</strong><span>Closed</span>" in landing_body
    assert "<strong>1</strong><span>Overdue</span>" in landing_body


def test_task_workspace_validation_errors_are_reported(client, app):
    with app.app_context():
        farm = _create_farm("Validation Farm")
        space = _create_space(farm, "VAL", "Validation")
        task = _create_task(space, "Audit water points", status="todo")
        db.session.commit()
        farm_id = str(farm.id)
        space_id = str(space.id)
        task_id = str(task.id)
        task_key = task.display_key

    response = client.post(
        "/tasks/spaces",
        data={
            "farm_id": farm_id,
            "key": "VAL",
            "name": "Duplicate",
            "description": "Should fail on duplicate key.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Task space key already exists" in response.data

    response = client.post(
        f"/tasks/{task_id}/status",
        data={"status": "done_now", "changed_by_name": "Ava"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Task status is invalid" in response.data

    response = client.post(
        f"/tasks/{task_id}/links",
        data={"link_type": "relates_to", "target_space_id": "", "target_task_key": "UNK-42"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Target task key was not found" in response.data

    response = client.post(
        f"/tasks/{task_id}/links",
        data={"link_type": "relates_to", "target_space_id": "", "target_task_key": task_key},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Tasks cannot link to themselves" in response.data

    response = client.post(
        f"/tasks/spaces/{space_id}/links",
        data={"link_type": "references", "target_space_id": space_id, "target_task_key": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Spaces cannot link to themselves" in response.data
