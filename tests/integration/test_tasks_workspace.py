from datetime import date, timedelta

from app.extensions import db
from app.models import Farm, Task, TaskComment, TaskLink, TaskSpace, TaskSpaceComment
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

    with app.app_context():
        task = Task.query.join(TaskSpace).filter(TaskSpace.key == "BRD").first()
        assert task is not None
        assert task.started_at is not None
        assert task.display_key == "BRD-1"


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
        source_task = _create_task(source_space, "Order fence wire", status="impeded", priority="highest", due_date=overdue_date)
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
    assert "<h2>Impeded</h2><p>1</p>" in landing_body
    assert "<h2>Overdue</h2><p>1</p>" in landing_body


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
