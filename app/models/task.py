from sqlalchemy import func

from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class TaskSpace(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "task_spaces"

    farm_id = db.Column(db.String(36), db.ForeignKey("farms.id"), nullable=False, index=True)
    key = db.Column(db.String(20), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)

    farm = db.relationship("Farm")
    tasks = db.relationship("Task", back_populates="space", cascade="all, delete-orphan")
    comments = db.relationship("TaskSpaceComment", back_populates="space", cascade="all, delete-orphan")
    outgoing_links = db.relationship(
        "TaskLink",
        foreign_keys="TaskLink.source_space_id",
        back_populates="source_space",
        cascade="all, delete-orphan",
    )
    incoming_links = db.relationship(
        "TaskLink",
        foreign_keys="TaskLink.target_space_id",
        back_populates="target_space",
    )


class Task(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "tasks"

    space_id = db.Column(db.String(36), db.ForeignKey("task_spaces.id"), nullable=False, index=True)
    task_number = db.Column(db.Integer, nullable=False)
    heading = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    tags_csv = db.Column(db.String(500), nullable=False, default="")
    reporter_name = db.Column(db.String(120), nullable=False)
    assignee_name = db.Column(db.String(120))
    status = db.Column(db.String(40), nullable=False, default="todo", index=True)
    priority = db.Column(db.String(20), nullable=False, default="low", index=True)
    original_estimate_days = db.Column(db.Numeric(8, 2))
    due_date = db.Column(db.Date, index=True)
    started_at = db.Column(db.DateTime(timezone=True), index=True)
    closed_at = db.Column(db.DateTime(timezone=True), index=True)

    space = db.relationship("TaskSpace", back_populates="tasks")
    transitions = db.relationship(
        "TaskStatusTransition",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    comments = db.relationship("TaskComment", back_populates="task", cascade="all, delete-orphan")
    outgoing_links = db.relationship(
        "TaskLink",
        foreign_keys="TaskLink.source_task_id",
        back_populates="source_task",
        cascade="all, delete-orphan",
    )
    incoming_links = db.relationship(
        "TaskLink",
        foreign_keys="TaskLink.target_task_id",
        back_populates="target_task",
    )

    __table_args__ = (db.UniqueConstraint("space_id", "task_number", name="uq_tasks_space_task_number"),)

    @property
    def display_key(self) -> str:
        if not self.space:
            return f"TASK-{self.task_number}"
        return f"{self.space.key}-{self.task_number}"


class TaskStatusTransition(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "task_status_transitions"

    task_id = db.Column(db.String(36), db.ForeignKey("tasks.id"), nullable=False, index=True)
    from_status = db.Column(db.String(40))
    to_status = db.Column(db.String(40), nullable=False)
    changed_by_name = db.Column(db.String(120), nullable=False)
    note = db.Column(db.Text)
    changed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    task = db.relationship("Task", back_populates="transitions")


class TaskComment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "task_comments"

    task_id = db.Column(db.String(36), db.ForeignKey("tasks.id"), nullable=False, index=True)
    author_name = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)

    task = db.relationship("Task", back_populates="comments")


class TaskSpaceComment(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "task_space_comments"

    space_id = db.Column(db.String(36), db.ForeignKey("task_spaces.id"), nullable=False, index=True)
    author_name = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)

    space = db.relationship("TaskSpace", back_populates="comments")


class TaskLink(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "task_links"

    source_task_id = db.Column(db.String(36), db.ForeignKey("tasks.id"), index=True)
    source_space_id = db.Column(db.String(36), db.ForeignKey("task_spaces.id"), index=True)
    target_task_id = db.Column(db.String(36), db.ForeignKey("tasks.id"), index=True)
    target_space_id = db.Column(db.String(36), db.ForeignKey("task_spaces.id"), index=True)
    link_type = db.Column(db.String(30), nullable=False)
    note = db.Column(db.Text)

    source_task = db.relationship("Task", foreign_keys=[source_task_id], back_populates="outgoing_links")
    source_space = db.relationship(
        "TaskSpace",
        foreign_keys=[source_space_id],
        back_populates="outgoing_links",
    )
    target_task = db.relationship("Task", foreign_keys=[target_task_id], back_populates="incoming_links")
    target_space = db.relationship(
        "TaskSpace",
        foreign_keys=[target_space_id],
        back_populates="incoming_links",
    )

    __table_args__ = (
        db.CheckConstraint(
            (
                "(source_task_id IS NOT NULL AND source_space_id IS NULL) "
                "OR (source_task_id IS NULL AND source_space_id IS NOT NULL)"
            ),
            name="ck_task_links_one_source",
        ),
        db.CheckConstraint(
            (
                "(target_task_id IS NOT NULL AND target_space_id IS NULL) "
                "OR (target_task_id IS NULL AND target_space_id IS NOT NULL)"
            ),
            name="ck_task_links_one_target",
        ),
    )
