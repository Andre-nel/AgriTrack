from datetime import date

from app.extensions import db
from app.models import Farm, Task, TaskSpace
from app.services.calendar_service import CalendarService
from app.services.task_service import TaskService


def _create_farm(name: str) -> Farm:
    farm = Farm(name=name, timezone="UTC")
    db.session.add(farm)
    db.session.flush()
    return farm


def _create_space(farm: Farm, key: str, name: str) -> TaskSpace:
    space = TaskSpace(farm_id=farm.id, key=key, name=name, description=f"{name} work")
    db.session.add(space)
    db.session.flush()
    return space


def _create_activity(
    farm: Farm,
    *,
    title: str,
    start_date: date,
    repeat_interval: int | None = None,
    repeat_unit: str | None = None,
    repeat_until: date | None = None,
    description: str = "",
):
    activity = CalendarService.create_activity(
        farm_id=farm.id,
        title=title,
        description=description,
        start_date=start_date,
        repeat_interval=repeat_interval,
        repeat_unit=repeat_unit,
        repeat_until=repeat_until,
    )
    db.session.flush()
    return activity


def _create_task(space: TaskSpace, heading: str, due_date: date) -> Task:
    task = TaskService.create_task(
        space=space,
        heading=heading,
        description=f"{heading} description",
        raw_tags="planning,calendar",
        reporter_name="Reporter",
        assignee_name="Assignee",
        status="todo",
        priority="low",
        original_estimate_days="1",
        due_date=due_date.isoformat(),
    )
    db.session.flush()
    return task


def test_dashboard_renders_calendar_nav(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/calendar"' in response.data
    assert b">Calendar<" in response.data


def test_calendar_page_loads_with_empty_state(client):
    response = client.get("/calendar")
    assert response.status_code == 200
    assert b"Calendar" in response.data
    assert b"Create Activity" in response.data
    assert b"calendar-create-panel" in response.data
    assert b"calendar-activity-modal" in response.data


def test_calendar_can_create_one_off_activity_and_render_in_year_and_month_views(client, app):
    with app.app_context():
        farm = _create_farm("Planner Farm")
        db.session.commit()
        farm_id = str(farm.id)

    response = client.post(
        "/calendar/activities",
        data={
            "view": "year",
            "year": "2026",
            "month": "4",
            "farm_id": farm_id,
            "title": "Pregnancy scanning",
            "description": "Annual scanning block.",
            "start_date": "2026-04-15",
            "repeat_interval": "",
            "repeat_unit": "",
            "repeat_until": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Pregnancy scanning" in body

    month_page = client.get(f"/calendar?view=month&year=2026&month=4&farm_id={farm_id}")
    assert month_page.status_code == 200
    month_body = month_page.data.decode("utf-8")
    assert "Pregnancy scanning" in month_body
    assert "Create Task From Activity" in month_body


def test_eight_month_recurring_activity_expands_in_calendar_and_detail(client, app):
    with app.app_context():
        farm = _create_farm("Recurring Farm")
        activity = _create_activity(
            farm,
            title="Drench sheep",
            start_date=date(2026, 1, 10),
            repeat_interval=8,
            repeat_unit="months",
            description="Predictable seasonal drench.",
        )
        db.session.commit()
        activity_id = str(activity.id)
        farm_id = str(farm.id)

    september_page = client.get(f"/calendar?view=month&year=2026&month=9&farm_id={farm_id}")
    assert september_page.status_code == 200
    assert "Drench sheep" in september_page.data.decode("utf-8")

    may_page = client.get(f"/calendar?view=month&year=2027&month=5&farm_id={farm_id}")
    assert may_page.status_code == 200
    assert "Drench sheep" in may_page.data.decode("utf-8")

    detail_page = client.get(f"/calendar/activities/{activity_id}")
    assert detail_page.status_code == 200
    detail_body = detail_page.data.decode("utf-8")
    assert "2026-09-10" in detail_body
    assert "2027-05-10" in detail_body


def test_monthly_recurrence_clamps_to_month_end(app):
    with app.app_context():
        farm = _create_farm("Clamp Farm")
        activity = _create_activity(
            farm,
            title="Month-end planning",
            start_date=date(2026, 1, 31),
            repeat_interval=1,
            repeat_unit="months",
        )
        occurrences = CalendarService.expand_activity_for_calendar(
            activity,
            range_start=date(2026, 1, 1),
            range_end=date(2026, 4, 30),
        )

    assert [row["date"] for row in occurrences] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_calendar_occurrence_move_and_skip_affect_only_target_occurrence(client, app):
    with app.app_context():
        farm = _create_farm("Exceptions Farm")
        activity = _create_activity(
            farm,
            title="Fence walk",
            start_date=date(2026, 3, 5),
            repeat_interval=1,
            repeat_unit="months",
            repeat_until=date(2026, 6, 5),
        )
        db.session.commit()
        activity_id = str(activity.id)
        farm_id = str(farm.id)

    move_response = client.post(
        f"/calendar/activities/{activity_id}/occurrences",
        data={
            "occurrence_date": "2026-04-05",
            "action": "move",
            "rescheduled_date": "2026-04-09",
            "note": "Shift around stock sale.",
        },
        follow_redirects=True,
    )
    assert move_response.status_code == 200
    move_body = move_response.data.decode("utf-8")
    assert "2026-04-09" in move_body
    assert "2026-05-05" in move_body

    skip_response = client.post(
        f"/calendar/activities/{activity_id}/occurrences",
        data={
            "occurrence_date": "2026-05-05",
            "action": "skip",
            "rescheduled_date": "",
            "note": "Not needed this month.",
        },
        follow_redirects=True,
    )
    assert skip_response.status_code == 200
    skip_body = skip_response.data.decode("utf-8")
    assert "Skipped" in skip_body
    assert "2026-06-05" in skip_body

    may_page = client.get(f"/calendar?view=month&year=2026&month=5&farm_id={farm_id}")
    assert may_page.status_code == 200
    may_body = may_page.data.decode("utf-8")
    assert "Fence walk" not in may_body


def test_moved_occurrence_appears_in_destination_month_when_original_is_outside_month(client, app):
    with app.app_context():
        farm = _create_farm("Move Farm")
        activity = _create_activity(
            farm,
            title="Teeth check",
            start_date=date(2026, 4, 28),
        )
        db.session.commit()
        activity_id = str(activity.id)
        farm_id = str(farm.id)

    response = client.post(
        f"/calendar/activities/{activity_id}/occurrences",
        data={
            "occurrence_date": "2026-04-28",
            "action": "move",
            "rescheduled_date": "2026-05-02",
            "note": "Bring it into the next yard visit.",
        },
    )
    assert response.status_code == 302

    may_page = client.get(f"/calendar?view=month&year=2026&month=5&farm_id={farm_id}")
    assert may_page.status_code == 200
    assert "Teeth check" in may_page.data.decode("utf-8")


def test_calendar_farm_filter_excludes_other_farms_activities_and_tasks(client, app):
    with app.app_context():
        visible_farm = _create_farm("Visible Farm")
        hidden_farm = _create_farm("Hidden Farm")
        visible_space = _create_space(visible_farm, "VIS", "Visible Space")
        hidden_space = _create_space(hidden_farm, "HID", "Hidden Space")
        _create_activity(visible_farm, title="Visible activity", start_date=date(2026, 4, 10))
        _create_activity(hidden_farm, title="Hidden activity", start_date=date(2026, 4, 10))
        _create_task(visible_space, "Visible task", date(2026, 4, 10))
        _create_task(hidden_space, "Hidden task", date(2026, 4, 10))
        db.session.commit()
        visible_farm_id = str(visible_farm.id)

    response = client.get(f"/calendar?view=month&year=2026&month=4&farm_id={visible_farm_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Visible activity" in body
    assert "Visible task" in body
    assert "Hidden activity" not in body
    assert "Hidden task" not in body


def test_due_dated_tasks_appear_and_activity_shortcut_prefills_new_task_form(client, app):
    with app.app_context():
        farm = _create_farm("Task Calendar Farm")
        space = _create_space(farm, "CAL", "Calendar Tasks")
        task = _create_task(space, "Book contractor", date(2026, 4, 20))
        activity = _create_activity(
            farm,
            title="Ram check",
            start_date=date(2026, 4, 22),
            description="Prepare the animals and tags.",
        )
        db.session.commit()
        farm_id = str(farm.id)
        space_id = str(space.id)
        activity_id = str(activity.id)
        task_id = str(task.id)

    calendar_page = client.get(f"/calendar?view=month&year=2026&month=4&farm_id={farm_id}")
    assert calendar_page.status_code == 200
    calendar_body = calendar_page.data.decode("utf-8")
    assert "Book contractor" in calendar_body
    assert "CAL-1" in calendar_body

    new_task_page = client.get(
        f"/tasks/new?farm_id={farm_id}&due_date=2026-04-22&heading=Ram%20check&source_activity_id={activity_id}&occurrence_date=2026-04-22"
    )
    assert new_task_page.status_code == 200
    new_task_body = new_task_page.data.decode("utf-8")
    assert 'value="2026-04-22"' in new_task_body
    assert 'value="Ram check"' in new_task_body
    assert "Prepare the animals and tags." in new_task_body

    create_response = client.post(
        "/tasks/new",
        data={
            "farm_id": farm_id,
            "space_id": space_id,
            "heading": "Ram check",
            "description": "Prepare the animals and tags.",
            "reporter_name": "Ava",
            "assignee_name": "Noah",
            "status": "todo",
            "priority": "high",
            "original_estimate_days": "1",
            "due_date": "2026-04-22",
            "tags": "calendar,animal health",
            "source_activity_id": activity_id,
            "occurrence_date": "2026-04-22",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    create_body = create_response.data.decode("utf-8")
    assert "Task CAL-2 created for 2026-04-22" in create_body
    assert "Ram check" in create_body

    with app.app_context():
        created_task = Task.query.filter(Task.id != task_id, Task.heading == "Ram check").first()
        assert created_task is not None
        assert created_task.due_date.isoformat() == "2026-04-22"


def test_month_view_has_day_level_create_activity_button_with_prefilled_date(client, app):
    with app.app_context():
        farm = _create_farm("Popup Farm")
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(f"/calendar?view=month&year=2026&month=4&farm_id={farm_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'data-open-activity-modal' in body
    assert 'data-start-date="2026-04-01"' in body
    assert 'id="calendar-activity-modal-form"' in body


def test_year_view_hover_text_includes_day_item_names(client, app):
    with app.app_context():
        farm = _create_farm("Hover Farm")
        space = _create_space(farm, "HOV", "Hover Space")
        _create_activity(farm, title="Shearing prep", start_date=date(2026, 6, 12))
        _create_task(space, "Book shearers", date(2026, 6, 12))
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(f"/calendar?view=year&year=2026&farm_id={farm_id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'title="Activity: Shearing prep' in body
    assert "TO DO: Book shearers" in body
