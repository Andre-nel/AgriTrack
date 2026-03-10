from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from flask import url_for
from sqlalchemy import and_, or_
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import CalendarActivity, CalendarActivityException, Task, TaskSpace
from app.services.task_service import TASK_STATUS_LABELS

CALENDAR_REPEAT_UNITS = ("days", "weeks", "months", "years")
CALENDAR_EXCEPTION_ACTIONS = ("skip", "move")


class CalendarService:
    MAX_TITLE_LENGTH = 200
    MAX_DESCRIPTION_LENGTH = 5000

    @staticmethod
    def require_text(value: str | None, field_name: str, max_length: int) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def optional_text(value: str | None, max_length: int) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValueError(f"Value must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def parse_required_date(raw_value: str | date | None, field_name: str) -> date:
        if isinstance(raw_value, date):
            return raw_value
        text = (raw_value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be valid (YYYY-MM-DD)") from exc

    @staticmethod
    def parse_optional_date(raw_value: str | date | None, field_name: str) -> date | None:
        if raw_value is None or raw_value == "":
            return None
        return CalendarService.parse_required_date(raw_value, field_name)

    @staticmethod
    def parse_optional_positive_int(raw_value: str | int | None, field_name: str) -> int | None:
        if raw_value is None or raw_value == "":
            return None
        if isinstance(raw_value, int):
            value = raw_value
        else:
            text = (raw_value or "").strip()
            try:
                value = int(text)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a whole number") from exc
        if value <= 0:
            raise ValueError(f"{field_name} must be greater than 0")
        return value

    @staticmethod
    def validate_repeat_unit(raw_value: str | None) -> str | None:
        text = " ".join((raw_value or "").strip().lower().split())
        if not text:
            return None
        if text not in CALENDAR_REPEAT_UNITS:
            raise ValueError("Repeat unit is invalid")
        return text

    @staticmethod
    def validate_exception_action(raw_value: str | None) -> str:
        text = " ".join((raw_value or "").strip().lower().split())
        if text not in CALENDAR_EXCEPTION_ACTIONS:
            raise ValueError("Occurrence action is invalid")
        return text

    @classmethod
    def normalize_recurrence(
        cls,
        *,
        start_date: date,
        repeat_interval: str | int | None,
        repeat_unit: str | None,
        repeat_until: str | date | None,
    ) -> tuple[int | None, str | None, date | None]:
        normalized_interval = cls.parse_optional_positive_int(repeat_interval, "Repeat interval")
        normalized_unit = cls.validate_repeat_unit(repeat_unit)
        normalized_until = cls.parse_optional_date(repeat_until, "Repeat until")

        if normalized_interval is None and normalized_unit is None:
            if normalized_until is not None:
                raise ValueError("Repeat until requires a repeat interval and unit")
            return None, None, None
        if normalized_interval is None or normalized_unit is None:
            raise ValueError("Repeat interval and repeat unit must both be provided")
        if normalized_until is not None and normalized_until < start_date:
            raise ValueError("Repeat until must be on or after the start date")
        return normalized_interval, normalized_unit, normalized_until

    @classmethod
    def create_activity(
        cls,
        *,
        farm_id: str,
        title: str | None,
        description: str | None,
        start_date: str | date | None,
        repeat_interval: str | int | None,
        repeat_unit: str | None,
        repeat_until: str | date | None,
    ) -> CalendarActivity:
        parsed_start_date = cls.parse_required_date(start_date, "Start date")
        normalized_interval, normalized_unit, normalized_until = cls.normalize_recurrence(
            start_date=parsed_start_date,
            repeat_interval=repeat_interval,
            repeat_unit=repeat_unit,
            repeat_until=repeat_until,
        )
        activity = CalendarActivity(
            farm_id=(farm_id or "").strip(),
            title=cls.require_text(title, "Title", cls.MAX_TITLE_LENGTH),
            description=cls.optional_text(description, cls.MAX_DESCRIPTION_LENGTH),
            start_date=parsed_start_date,
            repeat_interval=normalized_interval,
            repeat_unit=normalized_unit,
            repeat_until=normalized_until,
        )
        if not activity.farm_id:
            raise ValueError("Farm is required")
        db.session.add(activity)
        return activity

    @classmethod
    def update_activity(
        cls,
        *,
        activity: CalendarActivity,
        farm_id: str,
        title: str | None,
        description: str | None,
        start_date: str | date | None,
        repeat_interval: str | int | None,
        repeat_unit: str | None,
        repeat_until: str | date | None,
    ) -> CalendarActivity:
        parsed_start_date = cls.parse_required_date(start_date, "Start date")
        normalized_interval, normalized_unit, normalized_until = cls.normalize_recurrence(
            start_date=parsed_start_date,
            repeat_interval=repeat_interval,
            repeat_unit=repeat_unit,
            repeat_until=repeat_until,
        )
        normalized_farm_id = (farm_id or "").strip()
        if not normalized_farm_id:
            raise ValueError("Farm is required")

        activity.farm_id = normalized_farm_id
        activity.title = cls.require_text(title, "Title", cls.MAX_TITLE_LENGTH)
        activity.description = cls.optional_text(description, cls.MAX_DESCRIPTION_LENGTH)
        activity.start_date = parsed_start_date
        activity.repeat_interval = normalized_interval
        activity.repeat_unit = normalized_unit
        activity.repeat_until = normalized_until
        cls.prune_invalid_exceptions(activity)
        return activity

    @classmethod
    def prune_invalid_exceptions(cls, activity: CalendarActivity) -> None:
        for exception in list(activity.exceptions):
            if not cls.is_valid_occurrence(activity, exception.occurrence_date):
                db.session.delete(exception)

    @classmethod
    def upsert_exception(
        cls,
        *,
        activity: CalendarActivity,
        occurrence_date: str | date | None,
        action: str | None,
        rescheduled_date: str | date | None,
        note: str | None,
    ) -> CalendarActivityException:
        parsed_occurrence_date = cls.parse_required_date(occurrence_date, "Occurrence date")
        if not cls.is_valid_occurrence(activity, parsed_occurrence_date):
            raise ValueError("Occurrence date is not part of this activity")

        normalized_action = cls.validate_exception_action(action)
        normalized_rescheduled_date = cls.parse_optional_date(rescheduled_date, "Rescheduled date")
        normalized_note = cls.optional_text(note, cls.MAX_DESCRIPTION_LENGTH)

        if normalized_action == "skip" and normalized_rescheduled_date is not None:
            raise ValueError("Skipped occurrences cannot have a rescheduled date")
        if normalized_action == "move" and normalized_rescheduled_date is None:
            raise ValueError("Moved occurrences require a rescheduled date")

        existing = CalendarActivityException.query.filter_by(
            calendar_activity_id=activity.id,
            occurrence_date=parsed_occurrence_date,
        ).first()
        if existing is None:
            existing = CalendarActivityException(
                calendar_activity_id=activity.id,
                occurrence_date=parsed_occurrence_date,
            )
            db.session.add(existing)

        existing.action = normalized_action
        existing.rescheduled_date = normalized_rescheduled_date
        existing.note = normalized_note
        return existing

    @staticmethod
    def clear_exception(activity: CalendarActivity, occurrence_date: str | date | None) -> None:
        parsed_occurrence_date = CalendarService.parse_required_date(occurrence_date, "Occurrence date")
        existing = CalendarActivityException.query.filter_by(
            calendar_activity_id=activity.id,
            occurrence_date=parsed_occurrence_date,
        ).first()
        if existing is not None:
            db.session.delete(existing)

    @staticmethod
    def is_recurring(activity: CalendarActivity) -> bool:
        return activity.repeat_interval is not None and activity.repeat_unit is not None

    @staticmethod
    def recurrence_summary(activity: CalendarActivity) -> str:
        if not CalendarService.is_recurring(activity):
            return "One-off activity"
        unit = activity.repeat_unit[:-1] if activity.repeat_interval == 1 and activity.repeat_unit.endswith("s") else activity.repeat_unit
        summary = f"Every {activity.repeat_interval} {unit}"
        if activity.repeat_until:
            summary = f"{summary} until {activity.repeat_until.isoformat()}"
        return summary

    @staticmethod
    def _shift_months(value: date, months: int) -> date:
        month_index = value.month - 1 + months
        year = value.year + (month_index // 12)
        month = (month_index % 12) + 1
        day = min(value.day, monthrange(year, month)[1])
        return date(year, month, day)

    @staticmethod
    def _shift_years(value: date, years: int) -> date:
        target_year = value.year + years
        day = min(value.day, monthrange(target_year, value.month)[1])
        return date(target_year, value.month, day)

    @classmethod
    def add_interval(cls, value: date, repeat_interval: int, repeat_unit: str) -> date:
        if repeat_unit == "days":
            return value + timedelta(days=repeat_interval)
        if repeat_unit == "weeks":
            return value + timedelta(weeks=repeat_interval)
        if repeat_unit == "months":
            return cls._shift_months(value, repeat_interval)
        if repeat_unit == "years":
            return cls._shift_years(value, repeat_interval)
        raise ValueError("Repeat unit is invalid")

    @classmethod
    def occurrence_at_index(cls, activity: CalendarActivity, occurrence_index: int) -> date:
        if occurrence_index < 0:
            raise ValueError("Occurrence index must not be negative")
        if not cls.is_recurring(activity):
            if occurrence_index == 0:
                return activity.start_date
            raise ValueError("Non-recurring activities have only one occurrence")
        if activity.repeat_unit == "days":
            return activity.start_date + timedelta(days=activity.repeat_interval * occurrence_index)
        if activity.repeat_unit == "weeks":
            return activity.start_date + timedelta(weeks=activity.repeat_interval * occurrence_index)
        if activity.repeat_unit == "months":
            return cls._shift_months(activity.start_date, activity.repeat_interval * occurrence_index)
        if activity.repeat_unit == "years":
            return cls._shift_years(activity.start_date, activity.repeat_interval * occurrence_index)
        raise ValueError("Repeat unit is invalid")

    @classmethod
    def is_valid_occurrence(cls, activity: CalendarActivity, occurrence_date: date) -> bool:
        if occurrence_date < activity.start_date:
            return False
        if not cls.is_recurring(activity):
            return occurrence_date == activity.start_date
        if activity.repeat_until and occurrence_date > activity.repeat_until:
            return False
        if activity.repeat_unit == "days":
            step_days = activity.repeat_interval
            return (occurrence_date - activity.start_date).days % step_days == 0
        if activity.repeat_unit == "weeks":
            step_days = activity.repeat_interval * 7
            return (occurrence_date - activity.start_date).days % step_days == 0

        occurrence_index = 0
        cursor = cls.occurrence_at_index(activity, occurrence_index)
        while cursor < occurrence_date:
            occurrence_index += 1
            cursor = cls.occurrence_at_index(activity, occurrence_index)
        return cursor == occurrence_date

    @classmethod
    def _activity_base_occurrences(
        cls,
        activity: CalendarActivity,
        *,
        range_start: date,
        range_end: date,
    ) -> list[date]:
        if range_end < range_start:
            return []
        if not cls.is_recurring(activity):
            if range_start <= activity.start_date <= range_end:
                return [activity.start_date]
            return []

        limit_date = min(activity.repeat_until or range_end, range_end)
        if limit_date < range_start:
            return []

        dates = []
        if activity.repeat_unit in {"days", "weeks"} and range_start > activity.start_date:
            occurrence_date = activity.start_date
            step_days = activity.repeat_interval * (7 if activity.repeat_unit == "weeks" else 1)
            diff = (range_start - occurrence_date).days
            occurrence_date = occurrence_date + timedelta(days=(diff // step_days) * step_days)
            while occurrence_date < range_start:
                occurrence_date += timedelta(days=step_days)
            while occurrence_date <= limit_date:
                if occurrence_date >= range_start:
                    dates.append(occurrence_date)
                occurrence_date += timedelta(days=step_days)
        else:
            occurrence_index = 0
            occurrence_date = cls.occurrence_at_index(activity, occurrence_index)
            while occurrence_date < range_start:
                occurrence_index += 1
                occurrence_date = cls.occurrence_at_index(activity, occurrence_index)
            while occurrence_date <= limit_date:
                if occurrence_date >= range_start:
                    dates.append(occurrence_date)
                occurrence_index += 1
                occurrence_date = cls.occurrence_at_index(activity, occurrence_index)
        return dates

    @staticmethod
    def _occurrence_exception_map(activity: CalendarActivity) -> dict[date, CalendarActivityException]:
        return {exception.occurrence_date: exception for exception in activity.exceptions}

    @classmethod
    def expand_activity_for_calendar(
        cls,
        activity: CalendarActivity,
        *,
        range_start: date,
        range_end: date,
    ) -> list[dict]:
        exception_map = cls._occurrence_exception_map(activity)
        rows = []

        for occurrence_date in cls._activity_base_occurrences(activity, range_start=range_start, range_end=range_end):
            exception = exception_map.get(occurrence_date)
            if exception and exception.action == "skip":
                continue
            display_date = exception.rescheduled_date if exception and exception.action == "move" else occurrence_date
            if display_date is None or not (range_start <= display_date <= range_end):
                continue
            rows.append(
                {
                    "date": display_date,
                    "original_date": occurrence_date,
                    "is_moved": bool(exception and exception.action == "move"),
                    "is_skipped": False,
                    "exception_action": exception.action if exception else None,
                    "exception_note": exception.note if exception else None,
                }
            )

        for exception in activity.exceptions:
            if exception.action != "move" or exception.rescheduled_date is None:
                continue
            if not (range_start <= exception.rescheduled_date <= range_end):
                continue
            if range_start <= exception.occurrence_date <= range_end:
                continue
            if not cls.is_valid_occurrence(activity, exception.occurrence_date):
                continue
            rows.append(
                {
                    "date": exception.rescheduled_date,
                    "original_date": exception.occurrence_date,
                    "is_moved": True,
                    "is_skipped": False,
                    "exception_action": exception.action,
                    "exception_note": exception.note,
                }
            )

        rows.sort(key=lambda row: (row["date"], row["original_date"]))
        return rows

    @classmethod
    def expand_activity_for_detail(
        cls,
        activity: CalendarActivity,
        *,
        range_start: date,
        range_end: date,
    ) -> list[dict]:
        exception_map = cls._occurrence_exception_map(activity)
        rows = []
        for occurrence_date in cls._activity_base_occurrences(activity, range_start=range_start, range_end=range_end):
            exception = exception_map.get(occurrence_date)
            display_date = exception.rescheduled_date if exception and exception.action == "move" else occurrence_date
            rows.append(
                {
                    "date": display_date or occurrence_date,
                    "original_date": occurrence_date,
                    "is_moved": bool(exception and exception.action == "move"),
                    "is_skipped": bool(exception and exception.action == "skip"),
                    "exception_action": exception.action if exception else None,
                    "exception_note": exception.note if exception else None,
                    "exception_rescheduled_date": exception.rescheduled_date if exception else None,
                    "create_task_url": url_for(
                        "tasks.new_task_page",
                        farm_id=activity.farm_id,
                        due_date=(display_date or occurrence_date).isoformat(),
                        heading=activity.title,
                        source_activity_id=activity.id,
                        occurrence_date=occurrence_date.isoformat(),
                    ),
                }
            )
        return rows

    @classmethod
    def _calendar_activity_query(
        cls,
        *,
        range_start: date,
        range_end: date,
        farm_id: str | None = None,
    ) -> list[CalendarActivity]:
        activity_query = CalendarActivity.query.options(
            selectinload(CalendarActivity.farm),
            selectinload(CalendarActivity.exceptions),
        )
        if farm_id:
            activity_query = activity_query.filter(CalendarActivity.farm_id == farm_id)

        activity_query = activity_query.filter(
            or_(
                and_(
                    CalendarActivity.repeat_interval.is_(None),
                    CalendarActivity.start_date >= range_start,
                    CalendarActivity.start_date <= range_end,
                ),
                and_(
                    CalendarActivity.repeat_interval.is_not(None),
                    CalendarActivity.start_date <= range_end,
                    or_(
                        CalendarActivity.repeat_until.is_(None),
                        CalendarActivity.repeat_until >= range_start,
                    ),
                ),
            )
        )

        move_query = CalendarActivity.query.options(
            selectinload(CalendarActivity.farm),
            selectinload(CalendarActivity.exceptions),
        ).join(CalendarActivityException)
        if farm_id:
            move_query = move_query.filter(CalendarActivity.farm_id == farm_id)
        move_query = move_query.filter(
            CalendarActivityException.action == "move",
            CalendarActivityException.rescheduled_date >= range_start,
            CalendarActivityException.rescheduled_date <= range_end,
        )

        activities = {activity.id: activity for activity in activity_query.all()}
        for activity in move_query.all():
            activities[activity.id] = activity
        return sorted(
            activities.values(),
            key=lambda activity: (
                activity.farm.name if activity.farm else "",
                activity.start_date,
                activity.title.lower(),
            ),
        )

    @classmethod
    def _task_items(
        cls,
        *,
        range_start: date,
        range_end: date,
        farm_id: str | None = None,
    ) -> list[dict]:
        task_query = (
            Task.query.options(selectinload(Task.space).selectinload(TaskSpace.farm))
            .join(TaskSpace)
            .filter(Task.due_date >= range_start, Task.due_date <= range_end)
        )
        if farm_id:
            task_query = task_query.filter(TaskSpace.farm_id == farm_id)

        items = []
        for task in task_query.order_by(Task.due_date.asc(), Task.heading.asc()).all():
            farm_name = task.space.farm.name if task.space and task.space.farm else "Unassigned"
            items.append(
                {
                    "kind": "task",
                    "date": task.due_date,
                    "title": task.heading,
                    "subtitle": f"{task.display_key} | {farm_name}",
                    "farm_name": farm_name,
                    "detail_url": url_for("tasks.task_detail", task_id=task.id),
                    "create_task_url": None,
                    "badge_text": TASK_STATUS_LABELS.get(task.status, "Task"),
                    "css_class": "calendar-item-task",
                }
            )
        return items

    @classmethod
    def build_calendar_data(
        cls,
        *,
        range_start: date,
        range_end: date,
        farm_id: str | None = None,
    ) -> dict:
        items_by_date: dict[date, list[dict]] = defaultdict(list)
        activity_occurrence_count = 0
        recurring_series_ids = set()

        activities = cls._calendar_activity_query(range_start=range_start, range_end=range_end, farm_id=farm_id)
        for activity in activities:
            occurrence_rows = cls.expand_activity_for_calendar(activity, range_start=range_start, range_end=range_end)
            for row in occurrence_rows:
                activity_occurrence_count += 1
                if cls.is_recurring(activity):
                    recurring_series_ids.add(activity.id)
                farm_name = activity.farm.name if activity.farm else "Unassigned"
                items_by_date[row["date"]].append(
                    {
                        "kind": "activity",
                        "date": row["date"],
                        "title": activity.title,
                        "subtitle": cls.recurrence_summary(activity),
                        "farm_name": farm_name,
                        "detail_url": url_for("calendar.activity_detail", activity_id=activity.id),
                        "create_task_url": url_for(
                            "tasks.new_task_page",
                            farm_id=activity.farm_id,
                            due_date=row["date"].isoformat(),
                            heading=activity.title,
                            source_activity_id=activity.id,
                            occurrence_date=row["original_date"].isoformat(),
                        ),
                        "badge_text": "Recurring" if cls.is_recurring(activity) else "Activity",
                        "css_class": "calendar-item-activity",
                    }
                )

        task_items = cls._task_items(range_start=range_start, range_end=range_end, farm_id=farm_id)
        for item in task_items:
            items_by_date[item["date"]].append(item)

        for day_items in items_by_date.values():
            day_items.sort(key=lambda item: (item["kind"], item["title"].lower(), item["farm_name"].lower()))

        return {
            "items_by_date": dict(items_by_date),
            "stats": {
                "activity_count": activity_occurrence_count,
                "recurring_series_count": len(recurring_series_ids),
                "task_count": len(task_items),
                "total_count": activity_occurrence_count + len(task_items),
            },
        }
