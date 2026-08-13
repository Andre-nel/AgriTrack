from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import WikiArticle
from app.services.wiki_service import WikiService


bp = Blueprint("wiki", __name__, url_prefix="/wiki")


def _form_values(source) -> dict:
    return {
        "title": (source.get("title") or "").strip(),
        "category": (source.get("category") or "General").strip(),
        "summary": (source.get("summary") or "").strip(),
        "content": (source.get("content") or "").strip(),
        "todos": (source.get("todos") or "").strip(),
    }


def _article_row(article: WikiArticle) -> dict:
    todos = WikiService.lines_from_text(article.todos)
    return {
        "article": article,
        "facts": WikiService.lines_from_text(article.content),
        "todos": todos,
        "todo_count": len(todos),
    }


def _category_rows(article_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for article_row in article_rows:
        article = article_row["article"]
        for row in rows:
            if row["name"] == article.category:
                row["articles"].append(article)
                break
        else:
            rows.append({"name": article.category, "articles": [article]})
    return rows


def _load_article(slug: str) -> WikiArticle:
    WikiService.ensure_default_articles()
    return WikiArticle.query.filter_by(slug=slug).first_or_404()


@bp.get("")
def index():
    WikiService.ensure_default_articles()
    filters = {"q": (request.args.get("q") or "").strip()}
    articles = WikiService.search_articles(query=filters["q"])
    article_rows = [_article_row(article) for article in articles]
    category_rows = _category_rows(article_rows)
    return render_template(
        "wiki/index.html",
        article_rows=article_rows,
        category_rows=category_rows,
        filters=filters,
        form_values=_form_values(request.args),
        summary={
            "article_count": len(article_rows),
            "category_count": len(category_rows),
            "todo_count": sum(row["todo_count"] for row in article_rows),
        },
    )


@bp.post("/articles")
def create_article():
    form_values = _form_values(request.form)
    try:
        article = WikiService.create_article(**form_values)
        db.session.commit()
        flash(f"Wiki section '{article.title}' created", "success")
        return redirect(url_for("wiki.detail", slug=article.slug))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("wiki.index", **form_values))


@bp.get("/<slug>")
def detail(slug: str):
    article = _load_article(slug)
    return render_template(
        "wiki/detail.html",
        row=_article_row(article),
        form_values={
            "title": article.title,
            "category": article.category,
            "summary": article.summary,
            "content": article.content,
            "todos": article.todos,
        },
    )


@bp.post("/<slug>/edit")
def edit_article(slug: str):
    article = _load_article(slug)
    form_values = _form_values(request.form)
    try:
        WikiService.update_article(article, **form_values)
        db.session.commit()
        flash("Wiki section updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("wiki.detail", slug=article.slug))


@bp.post("/<slug>/delete")
def delete_article(slug: str):
    article = _load_article(slug)
    db.session.delete(article)
    db.session.commit()
    flash("Wiki section deleted", "success")
    return redirect(url_for("wiki.index"))
