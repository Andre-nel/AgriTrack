"""Add task workspace tables.

Revision ID: f2c7d1a4b9e0
Revises: e5a2c3f4b8d1
Create Date: 2026-03-08 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2c7d1a4b9e0"
down_revision = "e5a2c3f4b8d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_spaces",
        sa.Column("farm_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(op.f("ix_task_spaces_farm_id"), "task_spaces", ["farm_id"], unique=False)
    op.create_index(op.f("ix_task_spaces_key"), "task_spaces", ["key"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("space_id", sa.String(length=36), nullable=False),
        sa.Column("task_number", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags_csv", sa.String(length=500), nullable=False),
        sa.Column("reporter_name", sa.String(length=120), nullable=False),
        sa.Column("assignee_name", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("original_estimate_days", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["task_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("space_id", "task_number", name="uq_tasks_space_task_number"),
    )
    op.create_index(op.f("ix_tasks_space_id"), "tasks", ["space_id"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_priority"), "tasks", ["priority"], unique=False)
    op.create_index(op.f("ix_tasks_due_date"), "tasks", ["due_date"], unique=False)
    op.create_index(op.f("ix_tasks_started_at"), "tasks", ["started_at"], unique=False)
    op.create_index(op.f("ix_tasks_closed_at"), "tasks", ["closed_at"], unique=False)

    op.create_table(
        "task_status_transitions",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("changed_by_name", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_status_transitions_task_id"), "task_status_transitions", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_status_transitions_changed_at"), "task_status_transitions", ["changed_at"], unique=False)

    op.create_table(
        "task_comments",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("author_name", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_comments_task_id"), "task_comments", ["task_id"], unique=False)

    op.create_table(
        "task_space_comments",
        sa.Column("space_id", sa.String(length=36), nullable=False),
        sa.Column("author_name", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["task_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_space_comments_space_id"), "task_space_comments", ["space_id"], unique=False)

    op.create_table(
        "task_links",
        sa.Column("source_task_id", sa.String(length=36), nullable=True),
        sa.Column("source_space_id", sa.String(length=36), nullable=True),
        sa.Column("target_task_id", sa.String(length=36), nullable=True),
        sa.Column("target_space_id", sa.String(length=36), nullable=True),
        sa.Column("link_type", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "(source_task_id IS NOT NULL AND source_space_id IS NULL) OR (source_task_id IS NULL AND source_space_id IS NOT NULL)",
            name="ck_task_links_one_source",
        ),
        sa.CheckConstraint(
            "(target_task_id IS NOT NULL AND target_space_id IS NULL) OR (target_task_id IS NULL AND target_space_id IS NOT NULL)",
            name="ck_task_links_one_target",
        ),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["source_space_id"], ["task_spaces.id"]),
        sa.ForeignKeyConstraint(["target_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["target_space_id"], ["task_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_links_source_task_id"), "task_links", ["source_task_id"], unique=False)
    op.create_index(op.f("ix_task_links_source_space_id"), "task_links", ["source_space_id"], unique=False)
    op.create_index(op.f("ix_task_links_target_task_id"), "task_links", ["target_task_id"], unique=False)
    op.create_index(op.f("ix_task_links_target_space_id"), "task_links", ["target_space_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_task_links_target_space_id"), table_name="task_links")
    op.drop_index(op.f("ix_task_links_target_task_id"), table_name="task_links")
    op.drop_index(op.f("ix_task_links_source_space_id"), table_name="task_links")
    op.drop_index(op.f("ix_task_links_source_task_id"), table_name="task_links")
    op.drop_table("task_links")

    op.drop_index(op.f("ix_task_space_comments_space_id"), table_name="task_space_comments")
    op.drop_table("task_space_comments")

    op.drop_index(op.f("ix_task_comments_task_id"), table_name="task_comments")
    op.drop_table("task_comments")

    op.drop_index(op.f("ix_task_status_transitions_changed_at"), table_name="task_status_transitions")
    op.drop_index(op.f("ix_task_status_transitions_task_id"), table_name="task_status_transitions")
    op.drop_table("task_status_transitions")

    op.drop_index(op.f("ix_tasks_closed_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_started_at"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_due_date"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_priority"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_space_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_index(op.f("ix_task_spaces_key"), table_name="task_spaces")
    op.drop_index(op.f("ix_task_spaces_farm_id"), table_name="task_spaces")
    op.drop_table("task_spaces")
