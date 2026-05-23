from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy import inspect as sa_inspect

from app.extensions import db
from app.models import (
    CalendarActivity,
    Farm,
    Mob,
    Paddock,
    Task,
    TaskComment,
    TaskEntityLink,
    TaskLink,
    TaskSpace,
    TaskSpaceComment,
    TaskStatusTransition,
    WaterAsset,
)
from app.services.task_service import (
    TASK_LINK_TYPE_LABELS,
    TASK_LINK_TYPES,
    TASK_PRIORITY_LABELS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    TASK_STATUS_LABELS,
    TaskService,
)

bp = Blueprint("tasks", __name__)
TASK_SUMMARY_OPEN_STATUSES = {"selected_for_execution", "in_progress", "ready_for_verification"}
TASK_ENTITY_TABLES = ("paddocks", "water_assets", "mobs")


def _space_redirect(space: TaskSpace):
    return redirect(url_for("tasks.space_detail", space_id=space.id))


def _task_redirect(task: Task):
    return redirect(url_for("tasks.task_detail", task_id=task.id))


def _format_linked_entity(task: Task | None = None, space: TaskSpace | None = None) -> dict:
    if task is not None:
        return {
            "kind": "Task",
            "label": f"{task.display_key} {task.heading}",
            "url": url_for("tasks.task_detail", task_id=task.id),
        }
    return {
        "kind": "Space",
        "label": f"{space.key} {space.name}",
        "url": url_for("tasks.space_detail", space_id=space.id),
    }


def _entity_tables_available() -> bool:
    inspector = sa_inspect(db.engine)
    return all(inspector.has_table(table_name) for table_name in TASK_ENTITY_TABLES)


def _source_list(source, *names: str) -> list[str]:
    values = []
    for name in names:
        if hasattr(source, "getlist"):
            values.extend(source.getlist(name))
        else:
            value = source.get(name) if hasattr(source, "get") else None
            if isinstance(value, (list, tuple)):
                values.extend(value)
            else:
                values.append(value)
    return TaskService.normalize_entity_ids(values)


def _farm_entity_options(farm_id: str | None) -> dict:
    if not _entity_tables_available():
        return {"paddocks": [], "water_assets": [], "mobs": []}
    paddock_query = Paddock.query.order_by(Paddock.name.asc())
    water_asset_query = WaterAsset.query.order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
    mob_query = Mob.query.order_by(Mob.name.asc())
    if farm_id:
        paddock_query = paddock_query.filter(Paddock.farm_id == farm_id)
        water_asset_query = water_asset_query.filter(WaterAsset.farm_id == farm_id)
        mob_query = mob_query.filter(Mob.farm_id == farm_id)
    return {
        "paddocks": paddock_query.all(),
        "water_assets": water_asset_query.all(),
        "mobs": mob_query.all(),
    }


def _entity_link_kind_and_target(link: TaskEntityLink) -> tuple[str, object]:
    if link.paddock is not None:
        return "Paddock", link.paddock
    if link.water_asset is not None:
        return "Water Asset", link.water_asset
    return "Mob", link.mob


def _entity_target_url(kind: str, target) -> str:
    if kind == "Paddock":
        return url_for("web.paddock_detail", paddock_id=target.id)
    if kind == "Water Asset":
        return url_for(
            "web.farm_water_workspace",
            farm_id=target.farm_id,
            open_asset_id=target.id,
            _anchor="water-assets",
        )
    return url_for("web.mob_detail", mob_id=target.id)


def _build_entity_link_rows(links: list[TaskEntityLink]) -> list[dict]:
    rows = []
    for link in links:
        kind, target = _entity_link_kind_and_target(link)
        rows.append(
            {
                "id": str(link.id),
                "kind": kind,
                "label": target.name,
                "url": _entity_target_url(kind, target),
            }
        )
    return rows


def _build_selected_entity_rows(form_values: dict) -> list[dict]:
    if not _entity_tables_available():
        return []
    rows = []
    for paddock in Paddock.query.filter(Paddock.id.in_(form_values["paddock_ids"])).order_by(Paddock.name.asc()).all():
        rows.append({"kind": "Paddock", "label": paddock.name, "url": _entity_target_url("Paddock", paddock)})
    for asset in WaterAsset.query.filter(WaterAsset.id.in_(form_values["water_asset_ids"])).order_by(
        WaterAsset.name.asc()
    ).all():
        rows.append({"kind": "Water Asset", "label": asset.name, "url": _entity_target_url("Water Asset", asset)})
    for mob in Mob.query.filter(Mob.id.in_(form_values["mob_ids"])).order_by(Mob.name.asc()).all():
        rows.append({"kind": "Mob", "label": mob.name, "url": _entity_target_url("Mob", mob)})
    return rows


def _build_link_rows(links: list[TaskLink], *, current_task: Task | None = None, current_space: TaskSpace | None = None):
    rows = []
    for link in links:
        is_outgoing = False
        if current_task is not None and link.source_task_id == current_task.id:
            is_outgoing = True
        if current_space is not None and link.source_space_id == current_space.id:
            is_outgoing = True
        rows.append(
            {
                "direction": "Outgoing" if is_outgoing else "Incoming",
                "type_label": TASK_LINK_TYPE_LABELS[link.link_type],
                "other": (
                    _format_linked_entity(task=link.target_task, space=link.target_space)
                    if is_outgoing
                    else _format_linked_entity(task=link.source_task, space=link.source_space)
                ),
                "note": link.note,
            }
        )
    return rows


def _build_comment_rows(comments, tz_name: str):
    return [
        {
            "author_name": comment.author_name,
            "body": comment.body,
            "created_at": TaskService.format_local_datetime(comment.created_at, tz_name),
        }
        for comment in comments
    ]


def _build_task_card(task: Task, tz_name: str) -> dict:
    return {
        "id": task.id,
        "display_key": task.display_key,
        "heading": task.heading,
        "description": task.description,
        "tags": TaskService.tags_from_csv(task.tags_csv),
        "reporter_name": task.reporter_name,
        "assignee_name": task.assignee_name,
        "status": task.status,
        "status_label": TASK_STATUS_LABELS[task.status],
        "priority": task.priority,
        "priority_label": TASK_PRIORITY_LABELS[task.priority],
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "is_overdue": TaskService.is_task_overdue(task, tz_name=tz_name),
        "original_estimate_days": TaskService.format_estimate(task.original_estimate_days),
        "started_at": TaskService.format_local_date(task.started_at, tz_name),
        "closed_at": TaskService.format_local_date(task.closed_at, tz_name),
        "duration": TaskService.format_duration(task.started_at, task.closed_at),
    }


def _build_space_summary(tasks: list[Task], tz_name: str) -> dict:
    return {
        "open_count": len([task for task in tasks if task.status != "closed"]),
        "todo_count": len([task for task in tasks if task.status == "todo"]),
        "active_open_count": len([task for task in tasks if task.status in TASK_SUMMARY_OPEN_STATUSES]),
        "closed_count": len([task for task in tasks if task.status == "closed"]),
        "overdue_count": len([task for task in tasks if TaskService.is_task_overdue(task, tz_name=tz_name)]),
        "impeded_count": len([task for task in tasks if task.status == "impeded"]),
        "task_count": len(tasks),
    }


def _build_space_card(space: TaskSpace) -> dict:
    tz_name = space.farm.timezone if space.farm else "UTC"
    return {
        "id": space.id,
        "key": space.key,
        "name": space.name,
        "description": space.description,
        "summary": _build_space_summary(list(space.tasks), tz_name),
    }


def _load_space(space_id: str) -> TaskSpace:
    return (
        TaskSpace.query.options(selectinload(TaskSpace.farm))
        .filter_by(id=space_id)
        .first_or_404()
    )


def _load_task(task_id: str) -> Task:
    return (
        Task.query.options(selectinload(Task.space).selectinload(TaskSpace.farm))
        .filter_by(id=task_id)
        .first_or_404()
    )


def _load_optional_activity(activity_id: str | None) -> CalendarActivity | None:
    normalized = (activity_id or "").strip()
    if not normalized:
        return None
    return (
        CalendarActivity.query.options(selectinload(CalendarActivity.farm))
        .filter_by(id=normalized)
        .first()
    )


def _build_new_task_form_values(source) -> tuple[dict, CalendarActivity | None]:
    source_activity = _load_optional_activity(source.get("source_activity_id"))
    selected_space_id = (source.get("space_id") or "").strip()
    selected_farm_id = (source.get("farm_id") or "").strip()
    paddock_ids = _source_list(source, "paddock_ids", "paddock_id")
    water_asset_ids = _source_list(source, "water_asset_ids", "water_asset_id")
    mob_ids = _source_list(source, "mob_ids", "mob_id")

    selected_space = None
    if selected_space_id:
        selected_space = TaskSpace.query.options(selectinload(TaskSpace.farm)).filter_by(id=selected_space_id).first()

    selected_entity_farm_ids = []
    if _entity_tables_available():
        for paddock in Paddock.query.filter(Paddock.id.in_(paddock_ids)).all():
            selected_entity_farm_ids.append(str(paddock.farm_id))
        for asset in WaterAsset.query.filter(WaterAsset.id.in_(water_asset_ids)).all():
            selected_entity_farm_ids.append(str(asset.farm_id))
        for mob in Mob.query.filter(Mob.id.in_(mob_ids)).all():
            selected_entity_farm_ids.append(str(mob.farm_id))

    if not selected_farm_id and source_activity is not None:
        selected_farm_id = source_activity.farm_id
    if not selected_farm_id and selected_space is not None:
        selected_farm_id = selected_space.farm_id
    if not selected_farm_id and len(set(selected_entity_farm_ids)) == 1:
        selected_farm_id = selected_entity_farm_ids[0]

    due_date = (source.get("due_date") or "").strip()
    occurrence_date = (source.get("occurrence_date") or "").strip()
    if not due_date and occurrence_date:
        due_date = occurrence_date

    heading = (source.get("heading") or "").strip()
    if not heading and source_activity is not None:
        heading = source_activity.title

    description = (source.get("description") or "").strip()
    if not description and source_activity is not None and source_activity.description:
        description = source_activity.description

    return (
        {
            "farm_id": selected_farm_id,
            "space_id": selected_space_id,
            "heading": heading,
            "description": description,
            "reporter_name": (source.get("reporter_name") or "").strip(),
            "assignee_name": (source.get("assignee_name") or "").strip(),
            "status": (source.get("status") or "todo").strip() or "todo",
            "priority": (source.get("priority") or "low").strip() or "low",
            "original_estimate_days": (source.get("original_estimate_days") or "").strip(),
            "due_date": due_date,
            "tags": (source.get("tags") or "").strip(),
            "source_activity_id": (source.get("source_activity_id") or "").strip(),
            "occurrence_date": occurrence_date,
            "paddock_ids": paddock_ids,
            "water_asset_ids": water_asset_ids,
            "mob_ids": mob_ids,
        },
        source_activity,
    )


def _render_new_task_form(*, form_values: dict, source_activity: CalendarActivity | None, status_code: int = 200):
    farms = Farm.query.order_by(Farm.name).all()
    space_query = TaskSpace.query.options(selectinload(TaskSpace.farm)).order_by(TaskSpace.key)
    if form_values["farm_id"]:
        space_query = space_query.filter(TaskSpace.farm_id == form_values["farm_id"])
    spaces = space_query.all()
    entity_linking_available = _entity_tables_available()
    entity_options = _farm_entity_options(form_values["farm_id"])
    return (
        render_template(
            "tasks/new.html",
            farms=farms,
            spaces=spaces,
            entity_linking_available=entity_linking_available,
            entity_options=entity_options,
            selected_entity_rows=_build_selected_entity_rows(form_values),
            form_values=form_values,
            source_activity=source_activity,
            create_status_options=[status for status in TASK_STATUSES if status != "closed"],
            priority_options=TASK_PRIORITIES,
            priority_labels=TASK_PRIORITY_LABELS,
        ),
        status_code,
    )


def _resolve_link_target(raw_space_id: str | None, raw_task_key: str | None):
    target_space_id = (raw_space_id or "").strip()
    target_task_key = (raw_task_key or "").strip()
    if target_space_id and target_task_key:
        raise ValueError("Choose either a target space or a target task key")
    if not target_space_id and not target_task_key:
        raise ValueError("A link target is required")
    if target_space_id:
        target_space = TaskSpace.query.filter_by(id=target_space_id).first()
        if not target_space:
            raise ValueError("Target space was not found")
        return {"task": None, "space": target_space}
    target_task = TaskService.resolve_task_display_key(target_task_key)
    if not target_task:
        raise ValueError("Target task key was not found")
    return {"task": target_task, "space": None}


@bp.get("/tasks")
def index():
    selected_farm_id = (request.args.get("farm_id") or "").strip()
    farms = Farm.query.order_by(Farm.name).all()
    space_query = TaskSpace.query.options(selectinload(TaskSpace.farm), selectinload(TaskSpace.tasks)).order_by(
        TaskSpace.key
    )
    if selected_farm_id:
        space_query = space_query.filter(TaskSpace.farm_id == selected_farm_id)
    spaces = space_query.all()

    grouped = defaultdict(list)
    summary = {
        "space_count": 0,
        "todo_count": 0,
        "active_open_count": 0,
        "impeded_count": 0,
        "closed_count": 0,
        "overdue_count": 0,
    }
    for space in spaces:
        card = _build_space_card(space)
        grouped[space.farm.name if space.farm else "Unassigned"].append(card)
        summary["space_count"] += 1
        summary["todo_count"] += card["summary"]["todo_count"]
        summary["active_open_count"] += card["summary"]["active_open_count"]
        summary["impeded_count"] += card["summary"]["impeded_count"]
        summary["closed_count"] += card["summary"]["closed_count"]
        summary["overdue_count"] += card["summary"]["overdue_count"]

    grouped_spaces = [
        {"farm_name": farm_name, "spaces": grouped[farm_name]}
        for farm_name in sorted(grouped.keys())
    ]
    return render_template(
        "tasks/index.html",
        farms=farms,
        grouped_spaces=grouped_spaces,
        selected_farm_id=selected_farm_id,
        summary=summary,
    )


@bp.get("/tasks/new")
def new_task_page():
    form_values, source_activity = _build_new_task_form_values(request.args)
    return _render_new_task_form(form_values=form_values, source_activity=source_activity)


@bp.post("/tasks/new")
def create_task_from_page():
    form_values, source_activity = _build_new_task_form_values(request.form)
    try:
        space_id = form_values["space_id"]
        selected_space = TaskSpace.query.options(selectinload(TaskSpace.farm)).filter_by(id=space_id).first()
        if selected_space is None:
            raise ValueError("Task space is required")
        if form_values["farm_id"] and selected_space.farm_id != form_values["farm_id"]:
            raise ValueError("Task space must belong to the selected farm")

        task = TaskService.create_task(
            space=selected_space,
            heading=request.form.get("heading"),
            description=request.form.get("description"),
            raw_tags=request.form.get("tags"),
            reporter_name=request.form.get("reporter_name"),
            assignee_name=request.form.get("assignee_name"),
            status=request.form.get("status") or "todo",
            priority=request.form.get("priority"),
            original_estimate_days=request.form.get("original_estimate_days"),
            due_date=request.form.get("due_date"),
        )
        TaskService.add_entity_links(
            task=task,
            paddock_ids=form_values["paddock_ids"],
            water_asset_ids=form_values["water_asset_ids"],
            mob_ids=form_values["mob_ids"],
        )
        db.session.commit()
        due_label = task.due_date.isoformat() if task.due_date else "no due date"
        flash(f"Task {task.display_key} created for {due_label}", "success")
        return redirect(url_for("tasks.task_detail", task_id=task.id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _render_new_task_form(form_values=form_values, source_activity=source_activity, status_code=200)


@bp.post("/tasks/spaces")
def create_space():
    farm_id = (request.form.get("farm_id") or "").strip()
    redirect_kwargs = {"farm_id": farm_id} if farm_id else {}
    try:
        farm = Farm.query.filter_by(id=farm_id).first()
        if not farm:
            raise ValueError("Task space farm is required")
        key = TaskService.normalize_space_key(request.form.get("key"))
        if TaskSpace.query.filter_by(key=key).first():
            raise ValueError("Task space key already exists")
        space = TaskSpace(
            farm_id=farm.id,
            key=key,
            name=TaskService.require_text(request.form.get("name"), "Task space name", 120),
            description=TaskService.require_text(
                request.form.get("description"),
                "Task space description",
                TaskService.MAX_DESCRIPTION_LENGTH,
            ),
        )
        db.session.add(space)
        db.session.commit()
        flash("Task space created", "success")
        return redirect(url_for("tasks.space_detail", space_id=space.id))
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("tasks.index", **redirect_kwargs))


@bp.get("/tasks/spaces/<space_id>")
def space_detail(space_id: str):
    space = _load_space(space_id)
    tasks = Task.query.filter_by(space_id=space.id).order_by(Task.task_number).all()
    tz_name = space.farm.timezone if space.farm else "UTC"
    summary = _build_space_summary(tasks, tz_name)
    board_columns = []
    for status in TASK_STATUSES:
        cards = [_build_task_card(task, tz_name) for task in tasks if task.status == status]
        board_columns.append(
            {"status": status, "label": TASK_STATUS_LABELS[status], "count": len(cards), "tasks": cards}
        )

    comments = (
        TaskSpaceComment.query.filter_by(space_id=space.id)
        .order_by(TaskSpaceComment.created_at.desc(), TaskSpaceComment.id.desc())
        .all()
    )
    links = (
        TaskLink.query.filter(
            (TaskLink.source_space_id == space.id) | (TaskLink.target_space_id == space.id)
        )
        .order_by(TaskLink.created_at.desc(), TaskLink.id.desc())
        .all()
    )
    return render_template(
        "tasks/space_detail.html",
        space=space,
        summary=summary,
        board_columns=board_columns,
        farms=Farm.query.order_by(Farm.name).all(),
        create_status_options=[status for status in TASK_STATUSES if status != "closed"],
        priority_options=TASK_PRIORITIES,
        priority_labels=TASK_PRIORITY_LABELS,
        link_type_options=TASK_LINK_TYPES,
        link_type_labels=TASK_LINK_TYPE_LABELS,
        comment_rows=_build_comment_rows(comments, tz_name),
        link_rows=_build_link_rows(links, current_space=space),
        entity_linking_available=_entity_tables_available(),
        entity_options=_farm_entity_options(space.farm_id),
        available_spaces=TaskSpace.query.filter(TaskSpace.id != space.id).order_by(TaskSpace.key).all(),
    )


@bp.post("/tasks/spaces/<space_id>/edit")
def edit_space(space_id: str):
    space = _load_space(space_id)
    try:
        farm_id = (request.form.get("farm_id") or "").strip()
        farm = Farm.query.filter_by(id=farm_id).first()
        if not farm:
            raise ValueError("Task space farm is required")
        key = TaskService.normalize_space_key(request.form.get("key"))
        if TaskSpace.query.filter(TaskSpace.key == key, TaskSpace.id != space.id).first():
            raise ValueError("Task space key already exists")
        space.farm_id = farm.id
        space.key = key
        space.name = TaskService.require_text(request.form.get("name"), "Task space name", 120)
        space.description = TaskService.require_text(
            request.form.get("description"),
            "Task space description",
            TaskService.MAX_DESCRIPTION_LENGTH,
        )
        db.session.commit()
        flash("Task space updated", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _space_redirect(space)


@bp.post("/tasks/spaces/<space_id>/tasks")
def create_task(space_id: str):
    space = _load_space(space_id)
    try:
        task = TaskService.create_task(
            space=space,
            heading=request.form.get("heading"),
            description=request.form.get("description"),
            raw_tags=request.form.get("tags"),
            reporter_name=request.form.get("reporter_name"),
            assignee_name=request.form.get("assignee_name"),
            status=request.form.get("status") or "todo",
            priority=request.form.get("priority"),
            original_estimate_days=request.form.get("original_estimate_days"),
            due_date=request.form.get("due_date"),
        )
        TaskService.add_entity_links(
            task=task,
            paddock_ids=request.form.getlist("paddock_ids"),
            water_asset_ids=request.form.getlist("water_asset_ids"),
            mob_ids=request.form.getlist("mob_ids"),
        )
        db.session.commit()
        flash(f"Task {task.display_key} created", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _space_redirect(space)


@bp.post("/tasks/spaces/<space_id>/comments")
def create_space_comment(space_id: str):
    space = _load_space(space_id)
    try:
        db.session.add(
            TaskSpaceComment(
                space_id=space.id,
                author_name=TaskService.require_text(request.form.get("author_name"), "Author", 120),
                body=TaskService.require_text(
                    request.form.get("body"),
                    "Comment",
                    TaskService.MAX_DESCRIPTION_LENGTH,
                ),
            )
        )
        db.session.commit()
        flash("Space comment added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _space_redirect(space)


@bp.post("/tasks/spaces/<space_id>/links")
def create_space_link(space_id: str):
    space = _load_space(space_id)
    try:
        target = _resolve_link_target(request.form.get("target_space_id"), request.form.get("target_task_key"))
        TaskService.create_link(
            source_space=space,
            target_space=target["space"],
            target_task=target["task"],
            link_type=request.form.get("link_type"),
            note=request.form.get("note"),
        )
        db.session.commit()
        flash("Space link added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _space_redirect(space)


@bp.get("/tasks/<task_id>")
def task_detail(task_id: str):
    task = _load_task(task_id)
    tz_name = task.space.farm.timezone if task.space and task.space.farm else "UTC"
    transitions = (
        TaskStatusTransition.query.filter_by(task_id=task.id)
        .order_by(TaskStatusTransition.changed_at.asc(), TaskStatusTransition.id.asc())
        .all()
    )
    comments = (
        TaskComment.query.filter_by(task_id=task.id)
        .order_by(TaskComment.created_at.desc(), TaskComment.id.desc())
        .all()
    )
    links = (
        TaskLink.query.filter((TaskLink.source_task_id == task.id) | (TaskLink.target_task_id == task.id))
        .order_by(TaskLink.created_at.desc(), TaskLink.id.desc())
        .all()
    )
    entity_links = (
        TaskEntityLink.query.options(
            selectinload(TaskEntityLink.paddock),
            selectinload(TaskEntityLink.water_asset),
            selectinload(TaskEntityLink.mob),
        )
        .filter_by(task_id=task.id)
        .order_by(TaskEntityLink.created_at.desc(), TaskEntityLink.id.desc())
        .all()
    )
    return render_template(
        "tasks/task_detail.html",
        task=task,
        task_card=_build_task_card(task, tz_name),
        transitions=[
            {
                "changed_at": TaskService.format_local_datetime(transition.changed_at, tz_name),
                "from_status_label": TASK_STATUS_LABELS.get(transition.from_status, "Created"),
                "to_status_label": TASK_STATUS_LABELS[transition.to_status],
                "changed_by_name": transition.changed_by_name,
                "note": transition.note,
            }
            for transition in transitions
        ],
        comment_rows=_build_comment_rows(comments, tz_name),
        link_rows=_build_link_rows(links, current_task=task),
        entity_link_rows=_build_entity_link_rows(entity_links),
        entity_linking_available=_entity_tables_available(),
        entity_options=_farm_entity_options(task.space.farm_id),
        priority_options=TASK_PRIORITIES,
        priority_labels=TASK_PRIORITY_LABELS,
        status_options=[status for status in TASK_STATUSES if status != task.status],
        status_labels=TASK_STATUS_LABELS,
        link_type_options=TASK_LINK_TYPES,
        link_type_labels=TASK_LINK_TYPE_LABELS,
        available_spaces=TaskSpace.query.order_by(TaskSpace.key).all(),
    )


@bp.post("/tasks/<task_id>/edit")
def edit_task(task_id: str):
    task = _load_task(task_id)
    try:
        TaskService.update_task_metadata(
            task=task,
            heading=request.form.get("heading"),
            description=request.form.get("description"),
            raw_tags=request.form.get("tags"),
            reporter_name=request.form.get("reporter_name"),
            assignee_name=request.form.get("assignee_name"),
            priority=request.form.get("priority"),
            original_estimate_days=request.form.get("original_estimate_days"),
            due_date=request.form.get("due_date"),
        )
        db.session.commit()
        flash("Task details updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _task_redirect(task)


@bp.post("/tasks/<task_id>/status")
def update_task_status(task_id: str):
    task = _load_task(task_id)
    try:
        TaskService.apply_status_transition(
            task,
            request.form.get("status"),
            request.form.get("changed_by_name"),
            request.form.get("note"),
        )
        db.session.commit()
        flash("Task status updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _task_redirect(task)


@bp.post("/tasks/<task_id>/entity-links")
def create_task_entity_links(task_id: str):
    task = _load_task(task_id)
    try:
        links = TaskService.add_entity_links(
            task=task,
            paddock_ids=request.form.getlist("paddock_ids"),
            water_asset_ids=request.form.getlist("water_asset_ids"),
            mob_ids=request.form.getlist("mob_ids"),
        )
        db.session.commit()
        if links:
            flash(f"Added {len(links)} farm entity link(s)", "success")
        else:
            flash("No new farm entity links were added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _task_redirect(task)


@bp.post("/tasks/<task_id>/entity-links/<link_id>/delete")
def delete_task_entity_link(task_id: str, link_id: str):
    task = _load_task(task_id)
    link = TaskEntityLink.query.filter_by(id=link_id, task_id=task.id).first_or_404()
    db.session.delete(link)
    db.session.commit()
    flash("Farm entity link removed", "success")
    return _task_redirect(task)


@bp.post("/tasks/<task_id>/comments")
def create_task_comment(task_id: str):
    task = _load_task(task_id)
    try:
        db.session.add(
            TaskComment(
                task_id=task.id,
                author_name=TaskService.require_text(request.form.get("author_name"), "Author", 120),
                body=TaskService.require_text(
                    request.form.get("body"),
                    "Comment",
                    TaskService.MAX_DESCRIPTION_LENGTH,
                ),
            )
        )
        db.session.commit()
        flash("Task comment added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _task_redirect(task)


@bp.post("/tasks/<task_id>/links")
def create_task_link(task_id: str):
    task = _load_task(task_id)
    try:
        target = _resolve_link_target(request.form.get("target_space_id"), request.form.get("target_task_key"))
        TaskService.create_link(
            source_task=task,
            target_space=target["space"],
            target_task=target["task"],
            link_type=request.form.get("link_type"),
            note=request.form.get("note"),
        )
        db.session.commit()
        flash("Task link added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _task_redirect(task)
