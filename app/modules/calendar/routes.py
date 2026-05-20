import calendar as calendar_lib
from datetime import date, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import CalendarActivity, Farm
from app.services.calendar_service import CALENDAR_REPEAT_UNITS, CalendarService

bp = Blueprint("calendar", __name__)
CALENDAR_VIEWS = {"year", "month"}


def _parse_query_date(raw_value: str | None) -> date | None:
    text = (raw_value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_year(raw_value: str | None, fallback: int) -> int:
    text = (raw_value or "").strip()
    if not text:
        return fallback
    try:
        return max(1, min(9999, int(text)))
    except ValueError:
        return fallback


def _parse_month(raw_value: str | None, fallback: int) -> int:
    text = (raw_value or "").strip()
    if not text:
        return fallback
    try:
        value = int(text)
    except ValueError:
        return fallback
    return value if 1 <= value <= 12 else fallback


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar_lib.monthrange(year, month)[1])


def _day_heading(day: date) -> str:
    return f"{calendar_lib.day_name[day.weekday()]}, {calendar_lib.month_name[day.month]} {day.day}, {day.year}"


def _item_count_label(count: int, singular: str, plural: str | None = None) -> str:
    item_label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {item_label}"


def _calendar_url(
    *,
    view: str,
    year: int,
    month: int | None = None,
    farm_id: str | None = None,
    selected_date: date | None = None,
) -> str:
    kwargs = {"view": view, "year": year}
    if month is not None:
        kwargs["month"] = month
    if farm_id:
        kwargs["farm_id"] = farm_id
    if selected_date is not None:
        kwargs["selected_date"] = selected_date.isoformat()
    return url_for("calendar.index", **kwargs)


def _activity_detail_url(activity_id: str, focus_date: date | None = None) -> str:
    kwargs = {"activity_id": activity_id}
    if focus_date is not None:
        kwargs["focus_date"] = focus_date.isoformat()
    return url_for("calendar.activity_detail", **kwargs)


def _calendar_state_from_request() -> dict:
    today = date.today()
    view = " ".join((request.values.get("view") or "year").strip().lower().split())
    if view not in CALENDAR_VIEWS:
        view = "year"
    year = _parse_year(request.values.get("year"), today.year)
    month = _parse_month(request.values.get("month"), today.month)
    farm_id = (request.values.get("farm_id") or "").strip()
    selected_date = _parse_query_date(request.values.get("selected_date"))
    return {
        "view": view,
        "year": year,
        "month": month,
        "farm_id": farm_id,
        "selected_date": selected_date,
    }


def _load_activity(activity_id: str) -> CalendarActivity:
    return (
        CalendarActivity.query.options(
            selectinload(CalendarActivity.farm),
            selectinload(CalendarActivity.exceptions),
        )
        .filter_by(id=activity_id)
        .first_or_404()
    )


def _build_day_cell(
    day: date,
    *,
    display_month: int,
    item_lookup: dict[date, list[dict]],
    occupancy_lookup: dict[date, list[dict]],
    selected_farm_id: str,
    selected_date: date | None,
    mini_mode: bool,
) -> dict:
    all_items = item_lookup.get(day, [])
    all_occupancy = occupancy_lookup.get(day, [])
    visible_items = all_items[:2] if mini_mode else all_items
    visible_occupancy = all_occupancy[:1] if mini_mode else all_occupancy
    create_task_url = url_for(
        "tasks.new_task_page",
        due_date=day.isoformat(),
        farm_id=selected_farm_id or None,
    )
    scheduled_activity_urls = {
        item["detail_url"] for item in all_items if item["kind"] == "activity"
    }
    hover_lines = []
    for occupancy in all_occupancy:
        if occupancy["is_start"] and occupancy["detail_url"] in scheduled_activity_urls:
            continue
        hover_lines.append(f"Occupied: {occupancy['title']} ({occupancy['duration_text']})")
    hover_lines.extend(
        (
            f"{item['badge_text']}: {item['title']} ({item['duration_text']})"
            if item.get("duration_text")
            else f"{item['badge_text']}: {item['title']}"
        )
        for item in all_items
    )
    return {
        "date": day,
        "day_number": day.day,
        "in_month": day.month == display_month,
        "is_today": day == date.today() and day.month == display_month,
        "is_selected": selected_date == day,
        "items": visible_items,
        "all_items": all_items,
        "occupancy": visible_occupancy,
        "all_occupancy": all_occupancy,
        "hover_text": "\n".join(hover_lines),
        "overflow_count": max(len(all_items) - len(visible_items), 0),
        "occupancy_overflow_count": max(len(all_occupancy) - len(visible_occupancy), 0),
        "month_url": _calendar_url(
            view="month",
            year=day.year,
            month=day.month,
            farm_id=selected_farm_id or None,
            selected_date=day,
        ),
        "create_task_url": create_task_url,
    }


def _build_month_grid(
    year: int,
    month: int,
    item_lookup: dict[date, list[dict]],
    occupancy_lookup: dict[date, list[dict]],
    *,
    selected_farm_id: str,
    selected_date: date | None,
    mini_mode: bool,
) -> list[list[dict]]:
    calendar_rows = []
    month_calendar = calendar_lib.Calendar(firstweekday=0)
    for week in month_calendar.monthdatescalendar(year, month):
        calendar_rows.append(
            [
                _build_day_cell(
                    day,
                    display_month=month,
                    item_lookup=item_lookup,
                    occupancy_lookup=occupancy_lookup,
                    selected_farm_id=selected_farm_id,
                    selected_date=selected_date,
                    mini_mode=mini_mode,
                )
                for day in week
            ]
        )
    return calendar_rows


@bp.get("/calendar")
def index():
    today = date.today()
    state = _calendar_state_from_request()
    farms = Farm.query.order_by(Farm.name).all()

    if state["view"] == "year":
        range_start = date(state["year"], 1, 1)
        range_end = date(state["year"], 12, 31)
    else:
        range_start = _month_start(state["year"], state["month"])
        range_end = _month_end(state["year"], state["month"])

    calendar_data = CalendarService.build_calendar_data(
        range_start=range_start,
        range_end=range_end,
        farm_id=state["farm_id"] or None,
    )

    if state["view"] == "year":
        year_months = []
        for month_number in range(1, 13):
            year_months.append(
                {
                    "month_number": month_number,
                    "month_label": calendar_lib.month_name[month_number],
                    "month_url": _calendar_url(
                        view="month",
                        year=state["year"],
                        month=month_number,
                        farm_id=state["farm_id"] or None,
                    ),
                    "weeks": _build_month_grid(
                        state["year"],
                        month_number,
                        calendar_data["items_by_date"],
                        calendar_data["occupancy_by_date"],
                        selected_farm_id=state["farm_id"],
                        selected_date=state["selected_date"],
                        mini_mode=True,
                    ),
                }
            )
        page_heading = f"Year Planner {state['year']}"
        previous_url = _calendar_url(
            view="year",
            year=state["year"] - 1,
            farm_id=state["farm_id"] or None,
        )
        next_url = _calendar_url(
            view="year",
            year=state["year"] + 1,
            farm_id=state["farm_id"] or None,
        )
        create_start_date = state["selected_date"] or (
            today if today.year == state["year"] else date(state["year"], 1, 1)
        )
    else:
        year_months = []
        previous_month = CalendarService.add_interval(range_start, -1, "months")
        next_month = CalendarService.add_interval(range_start, 1, "months")
        page_heading = f"{calendar_lib.month_name[state['month']]} {state['year']}"
        previous_url = _calendar_url(
            view="month",
            year=previous_month.year,
            month=previous_month.month,
            farm_id=state["farm_id"] or None,
        )
        next_url = _calendar_url(
            view="month",
            year=next_month.year,
            month=next_month.month,
            farm_id=state["farm_id"] or None,
        )
        create_start_date = state["selected_date"] or range_start

    selected_month_grid = _build_month_grid(
        state["year"],
        state["month"],
        calendar_data["items_by_date"],
        calendar_data["occupancy_by_date"],
        selected_farm_id=state["farm_id"],
        selected_date=state["selected_date"],
        mini_mode=False,
    )
    selected_day = None
    if state["selected_date"] is not None and range_start <= state["selected_date"] <= range_end:
        selected_day = _build_day_cell(
            state["selected_date"],
            display_month=state["selected_date"].month,
            item_lookup=calendar_data["items_by_date"],
            occupancy_lookup=calendar_data["occupancy_by_date"],
            selected_farm_id=state["farm_id"],
            selected_date=state["selected_date"],
            mini_mode=False,
        )
        activity_count = sum(1 for item in selected_day["all_items"] if item["kind"] == "activity")
        task_count = sum(1 for item in selected_day["all_items"] if item["kind"] == "task")
        total_count = len(selected_day["all_items"])
        occupancy_count = len(selected_day["all_occupancy"])
        selected_day["heading"] = _day_heading(selected_day["date"])
        selected_day["summary"] = (
            f"{selected_day['date'].isoformat()} | "
            f"{_item_count_label(total_count, 'scheduled item')} | "
            f"{_item_count_label(activity_count, 'activity')} | "
            f"{_item_count_label(task_count, 'task')}"
        )
        selected_day["occupancy_summary"] = (
            _item_count_label(occupancy_count, "occupying activity") if occupancy_count else None
        )

    return render_template(
        "calendar/index.html",
        farms=farms,
        repeat_units=CALENDAR_REPEAT_UNITS,
        page_heading=page_heading,
        selected_view=state["view"],
        selected_year=state["year"],
        selected_month=state["month"],
        selected_farm_id=state["farm_id"],
        selected_date=state["selected_date"],
        day_names=[calendar_lib.day_abbr[index] for index in range(7)],
        month_choices=[{"number": number, "label": calendar_lib.month_name[number]} for number in range(1, 13)],
        year_months=year_months,
        month_grid=selected_month_grid,
        stats=calendar_data["stats"],
        previous_url=previous_url,
        next_url=next_url,
        switch_to_year_url=_calendar_url(
            view="year",
            year=state["year"],
            farm_id=state["farm_id"] or None,
        ),
        switch_to_month_url=_calendar_url(
            view="month",
            year=state["year"],
            month=state["month"],
            farm_id=state["farm_id"] or None,
            selected_date=state["selected_date"],
        ),
        create_activity_defaults={
            "farm_id": state["farm_id"],
            "title": "",
            "description": "",
            "start_date": create_start_date.isoformat(),
            "duration_days": "1",
            "repeat_interval": "",
            "repeat_unit": "months",
            "repeat_until": "",
        },
        selected_day=selected_day,
        clear_selected_day_url=_calendar_url(
            view=state["view"],
            year=state["year"],
            month=state["month"] if state["view"] == "month" else None,
            farm_id=state["farm_id"] or None,
        ),
    )


@bp.post("/calendar/activities")
def create_activity():
    state = _calendar_state_from_request()
    farm_id = (request.form.get("farm_id") or "").strip()
    redirect_date = _parse_query_date(request.form.get("start_date")) or state["selected_date"]

    try:
        if not Farm.query.filter_by(id=farm_id).first():
            raise ValueError("Calendar activity farm is required")
        activity = CalendarService.create_activity(
            farm_id=farm_id,
            title=request.form.get("title"),
            description=request.form.get("description"),
            start_date=request.form.get("start_date"),
            duration_days=request.form.get("duration_days"),
            repeat_interval=request.form.get("repeat_interval"),
            repeat_unit=request.form.get("repeat_unit"),
            repeat_until=request.form.get("repeat_until"),
        )
        db.session.commit()
        flash(f"Calendar activity '{activity.title}' created", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(
        _calendar_url(
            view=state["view"],
            year=state["year"],
            month=state["month"],
            farm_id=state["farm_id"] or None,
            selected_date=redirect_date,
        )
    )


@bp.get("/calendar/activities/<activity_id>")
def activity_detail(activity_id: str):
    activity = _load_activity(activity_id)
    today = date.today()
    focus_date = _parse_query_date(request.args.get("focus_date")) or today
    range_start = date(focus_date.year, focus_date.month, 1)
    range_end = CalendarService.add_interval(range_start, 24, "months") - timedelta(days=1)
    occurrence_rows = CalendarService.expand_activity_for_detail(
        activity,
        range_start=range_start,
        range_end=range_end,
    )
    return render_template(
        "calendar/activity_detail.html",
        activity=activity,
        farms=Farm.query.order_by(Farm.name).all(),
        repeat_units=CALENDAR_REPEAT_UNITS,
        duration_summary=CalendarService.format_duration_days(activity.duration_days),
        recurrence_summary=CalendarService.recurrence_summary(activity),
        occurrence_rows=occurrence_rows,
        back_url=_calendar_url(
            view="month",
            year=activity.start_date.year,
            month=activity.start_date.month,
            farm_id=activity.farm_id,
            selected_date=activity.start_date,
        ),
    )


@bp.post("/calendar/activities/<activity_id>/edit")
def edit_activity(activity_id: str):
    activity = _load_activity(activity_id)
    try:
        farm_id = (request.form.get("farm_id") or "").strip()
        if not Farm.query.filter_by(id=farm_id).first():
            raise ValueError("Calendar activity farm is required")
        CalendarService.update_activity(
            activity=activity,
            farm_id=farm_id,
            title=request.form.get("title"),
            description=request.form.get("description"),
            start_date=request.form.get("start_date"),
            duration_days=request.form.get("duration_days"),
            repeat_interval=request.form.get("repeat_interval"),
            repeat_unit=request.form.get("repeat_unit"),
            repeat_until=request.form.get("repeat_until"),
        )
        db.session.commit()
        flash("Calendar activity updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(_activity_detail_url(activity.id))


@bp.post("/calendar/activities/<activity_id>/delete")
def delete_activity(activity_id: str):
    activity = _load_activity(activity_id)
    redirect_url = _calendar_url(
        view="month",
        year=activity.start_date.year,
        month=activity.start_date.month,
        farm_id=activity.farm_id,
        selected_date=activity.start_date,
    )
    db.session.delete(activity)
    db.session.commit()
    flash("Calendar activity deleted", "success")
    return redirect(redirect_url)


@bp.post("/calendar/activities/<activity_id>/occurrences")
def create_occurrence_exception(activity_id: str):
    activity = _load_activity(activity_id)
    focus_date = (
        _parse_query_date(request.form.get("rescheduled_date"))
        or _parse_query_date(request.form.get("occurrence_date"))
    )
    try:
        exception = CalendarService.upsert_exception(
            activity=activity,
            occurrence_date=request.form.get("occurrence_date"),
            action=request.form.get("action"),
            rescheduled_date=request.form.get("rescheduled_date"),
            note=request.form.get("note"),
        )
        db.session.commit()
        focus_date = (
            exception.rescheduled_date
            if exception.action == "move" and exception.rescheduled_date is not None
            else exception.occurrence_date
        )
        flash("Occurrence override saved", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(_activity_detail_url(activity.id, focus_date))


@bp.post("/calendar/activities/<activity_id>/occurrences/<occurrence_date>/clear")
def clear_occurrence_exception(activity_id: str, occurrence_date: str):
    activity = _load_activity(activity_id)
    focus_date = _parse_query_date(occurrence_date)
    CalendarService.clear_exception(activity, occurrence_date)
    db.session.commit()
    flash("Occurrence override cleared", "success")
    return redirect(_activity_detail_url(activity.id, focus_date))
