from flask import Blueprint, abort, render_template, request

from app.services.wiki_service import WikiService


bp = Blueprint("wiki", __name__, url_prefix="/wiki")


@bp.get("")
def index():
    pages = WikiService.load_pages()
    filters = {"q": (request.args.get("q") or "").strip()}
    page_rows = WikiService.search_pages(filters["q"], pages=pages)
    recent_source = page_rows if filters["q"] else pages
    validation_errors = WikiService.validation_errors(pages)
    return render_template(
        "wiki/index.html",
        filters=filters,
        page_rows=page_rows,
        tree_rows=WikiService.page_tree(page_rows),
        tag_rows=WikiService.tag_counts(pages),
        recent_pages=sorted(recent_source, key=lambda page: page.updated_at, reverse=True)[:5],
        summary={
            "page_count": len(page_rows),
            "tag_count": len(WikiService.tag_counts(pages)),
            "draft_count": len([page for page in pages if page.status == "draft"]),
            "validation_error_count": len(validation_errors),
        },
    )


@bp.get("/<page_id>")
def detail(page_id: str):
    pages = WikiService.load_pages()
    page = next((candidate for candidate in pages if candidate.id == page_id), None)
    if page is None:
        abort(404)
    page_validation_errors = [
        error
        for error in WikiService.validation_errors(pages)
        if error.startswith(f"{page.relative_path}:")
    ]
    return render_template(
        "wiki/page.html",
        page=page,
        backlinks=WikiService.backlinks(page, pages),
        breadcrumbs=WikiService.breadcrumbs(page),
        validation_errors=page_validation_errors,
    )
