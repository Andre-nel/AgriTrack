"""Add wiki articles table.

Revision ID: 8d7e6c5b4a32
Revises: 5a1c9e7d2b44
Create Date: 2026-08-13 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8d7e6c5b4a32"
down_revision = "5a1c9e7d2b44"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wiki_articles",
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("todos", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_wiki_articles_category"), "wiki_articles", ["category"], unique=False)
    op.create_index(op.f("ix_wiki_articles_slug"), "wiki_articles", ["slug"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_wiki_articles_slug"), table_name="wiki_articles")
    op.drop_index(op.f("ix_wiki_articles_category"), table_name="wiki_articles")
    op.drop_table("wiki_articles")
