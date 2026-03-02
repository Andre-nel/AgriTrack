"""Allow reusing mob names after archive by scoping uniqueness to active mobs.

Revision ID: c2a7d9b41f6e
Revises: b7c931f9e2aa
Create Date: 2026-03-02 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2a7d9b41f6e"
down_revision = "b7c931f9e2aa"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mobs", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_mob_farm_name", type_="unique")

    dialect = op.get_bind().dialect.name
    where = sa.text("status = 'active'")
    if dialect == "sqlite":
        op.create_index(
            "uq_mob_farm_name_active",
            "mobs",
            ["farm_id", "name"],
            unique=True,
            sqlite_where=where,
        )
    elif dialect == "postgresql":
        op.create_index(
            "uq_mob_farm_name_active",
            "mobs",
            ["farm_id", "name"],
            unique=True,
            postgresql_where=where,
        )
    else:
        op.create_index("uq_mob_farm_name_active", "mobs", ["farm_id", "name", "status"], unique=True)


def downgrade():
    op.drop_index("uq_mob_farm_name_active", table_name="mobs")

    with op.batch_alter_table("mobs", recreate="always") as batch_op:
        batch_op.create_unique_constraint("uq_mob_farm_name", ["farm_id", "name"])
