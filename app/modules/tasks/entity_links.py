from collections import defaultdict

from flask import url_for
from sqlalchemy.orm import selectinload

from app.models import Task, TaskEntityLink, TaskSpace
from app.services.task_service import TASK_PRIORITY_LABELS, TASK_STATUS_LABELS, TaskService


def linked_task_row(task: Task, tz_name: str) -> dict:
    return {
        "id": str(task.id),
        "display_key": task.display_key,
        "heading": task.heading,
        "status": task.status,
        "status_label": TASK_STATUS_LABELS[task.status],
        "priority": task.priority,
        "priority_label": TASK_PRIORITY_LABELS[task.priority],
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "is_overdue": TaskService.is_task_overdue(task, tz_name=tz_name),
        "url": url_for("tasks.task_detail", task_id=task.id),
    }


def linked_task_rows_for_entity(
    *,
    paddock_id: str | None = None,
    water_asset_id: str | None = None,
    mob_id: str | None = None,
    fence_section_id: str | None = None,
    tz_name: str = "SAST",
) -> list[dict]:
    query = TaskEntityLink.query.options(
        selectinload(TaskEntityLink.task).selectinload(Task.space).selectinload(TaskSpace.farm)
    )
    if paddock_id is not None:
        query = query.filter(TaskEntityLink.paddock_id == paddock_id)
    elif water_asset_id is not None:
        query = query.filter(TaskEntityLink.water_asset_id == water_asset_id)
    elif mob_id is not None:
        query = query.filter(TaskEntityLink.mob_id == mob_id)
    else:
        query = query.filter(TaskEntityLink.fence_section_id == fence_section_id)
    links = query.join(Task).order_by(Task.created_at.desc(), Task.id.desc()).all()
    return [linked_task_row(link.task, tz_name) for link in links if link.task is not None]


def linked_task_rows_by_water_asset(asset_ids: list[str], tz_name: str) -> dict[str, list[dict]]:
    if not asset_ids:
        return {}
    links = (
        TaskEntityLink.query.options(
            selectinload(TaskEntityLink.task).selectinload(Task.space).selectinload(TaskSpace.farm)
        )
        .filter(TaskEntityLink.water_asset_id.in_(asset_ids))
        .join(Task)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .all()
    )
    rows_by_asset = defaultdict(list)
    for link in links:
        if link.task is None or link.water_asset_id is None:
            continue
        rows_by_asset[str(link.water_asset_id)].append(linked_task_row(link.task, tz_name))
    return dict(rows_by_asset)


def linked_task_rows_by_fence_section(section_ids: list[str], tz_name: str) -> dict[str, list[dict]]:
    if not section_ids:
        return {}
    links = (
        TaskEntityLink.query.options(
            selectinload(TaskEntityLink.task).selectinload(Task.space).selectinload(TaskSpace.farm)
        )
        .filter(TaskEntityLink.fence_section_id.in_(section_ids))
        .join(Task)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .all()
    )
    rows_by_section = defaultdict(list)
    for link in links:
        if link.task is None or link.fence_section_id is None:
            continue
        rows_by_section[str(link.fence_section_id)].append(linked_task_row(link.task, tz_name))
    return dict(rows_by_section)
