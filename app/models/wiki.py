from app.extensions import db
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class WikiArticle(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "wiki_articles"

    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)
    title = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), nullable=False, default="General", index=True)
    summary = db.Column(db.Text, nullable=False, default="")
    content = db.Column(db.Text, nullable=False, default="")
    todos = db.Column(db.Text, nullable=False, default="")
