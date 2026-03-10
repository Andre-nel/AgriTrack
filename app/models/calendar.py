from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class CalendarActivity(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "calendar_activities"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.Date, nullable=False, index=True)
    repeat_interval = db.Column(db.Integer)
    repeat_unit = db.Column(db.String(10))
    repeat_until = db.Column(db.Date, index=True)

    farm = db.relationship("Farm")
    exceptions = db.relationship(
        "CalendarActivityException",
        back_populates="activity",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint(
            (
                "(repeat_interval IS NULL AND repeat_unit IS NULL AND repeat_until IS NULL) "
                "OR (repeat_interval IS NOT NULL AND repeat_unit IS NOT NULL)"
            ),
            name="ck_calendar_activities_repeat_fields",
        ),
        db.CheckConstraint(
            "repeat_interval IS NULL OR repeat_interval > 0",
            name="ck_calendar_activities_repeat_interval_positive",
        ),
        db.CheckConstraint(
            "repeat_unit IS NULL OR repeat_unit IN ('days', 'weeks', 'months', 'years')",
            name="ck_calendar_activities_repeat_unit_valid",
        ),
        db.CheckConstraint(
            "repeat_until IS NULL OR repeat_until >= start_date",
            name="ck_calendar_activities_repeat_until_after_start",
        ),
    )


class CalendarActivityException(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "calendar_activity_exceptions"

    calendar_activity_id = db.Column(
        db.String(36),
        db.ForeignKey("calendar_activities.id"),
        nullable=False,
        index=True,
    )
    occurrence_date = db.Column(db.Date, nullable=False, index=True)
    action = db.Column(db.String(10), nullable=False)
    rescheduled_date = db.Column(db.Date, index=True)
    note = db.Column(db.Text)

    activity = db.relationship("CalendarActivity", back_populates="exceptions")

    __table_args__ = (
        db.CheckConstraint(
            "action IN ('skip', 'move')",
            name="ck_calendar_activity_exceptions_action_valid",
        ),
        db.CheckConstraint(
            (
                "(action = 'skip' AND rescheduled_date IS NULL) "
                "OR (action = 'move' AND rescheduled_date IS NOT NULL)"
            ),
            name="ck_calendar_activity_exceptions_reschedule_pair",
        ),
        db.UniqueConstraint(
            "calendar_activity_id",
            "occurrence_date",
            name="uq_calendar_activity_exceptions_activity_occurrence",
        ),
    )
