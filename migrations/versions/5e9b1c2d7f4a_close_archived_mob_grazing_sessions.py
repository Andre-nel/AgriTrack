"""Close stale grazing sessions for archived mobs.

Revision ID: 5e9b1c2d7f4a
Revises: 4d8f2a1b6c7e
Create Date: 2026-04-02 09:40:00.000000
"""

from datetime import timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5e9b1c2d7f4a"
down_revision = "4d8f2a1b6c7e"
branch_labels = None
depends_on = None


def _normalize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _resolved_close_at(session_start, mob_updated_at):
    session_start_dt = _normalize_datetime(session_start)
    mob_updated_at_dt = _normalize_datetime(mob_updated_at)
    if mob_updated_at_dt is None:
        return session_start
    if session_start_dt is not None and mob_updated_at_dt < session_start_dt:
        return session_start
    return mob_updated_at


def upgrade():
    bind = op.get_bind()

    grazing_sessions = sa.table(
        "grazing_sessions",
        sa.column("id", sa.String(length=36)),
        sa.column("mob_id", sa.String(length=36)),
        sa.column("start_at", sa.DateTime(timezone=True)),
        sa.column("end_at", sa.DateTime(timezone=True)),
    )
    mobs = sa.table(
        "mobs",
        sa.column("id", sa.String(length=36)),
        sa.column("status", sa.String(length=20)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    lsu_history = sa.table(
        "grazing_allocation_lsu_history",
        sa.column("id", sa.String(length=36)),
        sa.column("grazing_session_id", sa.String(length=36)),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
    )

    stale_sessions = bind.execute(
        sa.select(
            grazing_sessions.c.id.label("session_id"),
            grazing_sessions.c.start_at.label("session_start_at"),
            mobs.c.updated_at.label("mob_updated_at"),
        )
        .select_from(grazing_sessions.join(mobs, grazing_sessions.c.mob_id == mobs.c.id))
        .where(
            grazing_sessions.c.end_at.is_(None),
            mobs.c.status == "archived",
        )
    ).mappings().all()

    for session_row in stale_sessions:
        close_at = _resolved_close_at(
            session_row["session_start_at"],
            session_row["mob_updated_at"],
        )
        if close_at is None:
            continue

        bind.execute(
            grazing_sessions.update()
            .where(grazing_sessions.c.id == session_row["session_id"])
            .values(end_at=close_at)
        )

        open_history_rows = bind.execute(
            sa.select(
                lsu_history.c.id.label("history_id"),
                lsu_history.c.effective_from,
            ).where(
                lsu_history.c.grazing_session_id == session_row["session_id"],
                lsu_history.c.effective_to.is_(None),
            )
        ).mappings().all()

        close_at_dt = _normalize_datetime(close_at)
        for history_row in open_history_rows:
            row_from_dt = _normalize_datetime(history_row["effective_from"])
            if row_from_dt is not None and close_at_dt is not None and row_from_dt >= close_at_dt:
                bind.execute(
                    lsu_history.delete().where(lsu_history.c.id == history_row["history_id"])
                )
                continue

            bind.execute(
                lsu_history.update()
                .where(lsu_history.c.id == history_row["history_id"])
                .values(effective_to=close_at)
            )


def downgrade():
    # Irreversible data repair.
    pass
