from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_ROOT = REPO_ROOT / "docs" / "wiki"
REQUIRED_METADATA = {"id", "title", "tags", "status", "risk_level", "type"}
VALID_STATUSES = {"draft", "reviewed", "deprecated"}
VETERINARY_FIELDS = {
    "species",
    "animal_class",
    "purpose",
    "product",
    "active_ingredient",
    "product_concentration",
    "dose_units",
    "route",
    "frequency",
    "contraindications",
    "withdrawal_periods",
    "call_vet_conditions",
    "sources",
    "approval_status",
}


@dataclass(frozen=True)
class WikiHeading:
    level: int
    text: str
    anchor: str


@dataclass(frozen=True)
class WikiPage:
    id: str
    title: str
    parent: str
    tags: tuple[str, ...]
    status: str
    risk_level: str
    page_type: str
    relative_path: str
    source_path: Path
    body: str
    html: str
    toc: tuple[WikiHeading, ...]
    updated_at: str
    metadata: dict

    @property
    def excerpt(self) -> str:
        for line in self.body.splitlines():
            text = line.strip()
            if text and not text.startswith("#") and not text.startswith("- TODO:"):
                return text.replace("[[", "").replace("]]", "")
        return "No summary text recorded yet."


class WikiService:
    @classmethod
    def load_pages(cls) -> list[WikiPage]:
        paths = sorted(
            path
            for path in WIKI_ROOT.rglob("*.md")
            if "_inbox" not in path.relative_to(WIKI_ROOT).parts
        )
        raw_pages = [cls._parse_page(path) for path in paths]
        known_ids = {metadata.get("id", "") for metadata, _, _ in raw_pages}
        pages = []
        for metadata, body, path in raw_pages:
            html, toc = cls.render_markdown(body, known_ids)
            pages.append(cls._build_page(metadata, body, path, html, toc))
        return sorted(pages, key=lambda page: (page.relative_path.lower(), page.title.lower()))

    @classmethod
    def get_page(cls, page_id: str) -> WikiPage | None:
        normalized_id = page_id.strip()
        for page in cls.load_pages():
            if page.id == normalized_id:
                return page
        return None

    @classmethod
    def search_pages(cls, query: str | None = None, *, pages: list[WikiPage] | None = None) -> list[WikiPage]:
        loaded_pages = pages if pages is not None else cls.load_pages()
        normalized_query = (query or "").strip().lower()
        if not normalized_query:
            return loaded_pages
        return [
            page
            for page in loaded_pages
            if normalized_query
            in " ".join(
                [
                    page.id,
                    page.title,
                    page.parent,
                    page.status,
                    page.risk_level,
                    page.page_type,
                    " ".join(page.tags),
                    page.body,
                ]
            ).lower()
        ]

    @staticmethod
    def tag_counts(pages: list[WikiPage]) -> list[dict]:
        counts: dict[str, int] = {}
        for page in pages:
            for tag in page.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return [{"tag": tag, "count": count} for tag, count in sorted(counts.items())]

    @staticmethod
    def backlinks(target_page: WikiPage, pages: list[WikiPage]) -> list[WikiPage]:
        token = f"[[{target_page.id}"
        return [
            page
            for page in pages
            if page.id != target_page.id and (page.parent == target_page.id or token in page.body)
        ]

    @staticmethod
    def breadcrumbs(page: WikiPage) -> list[dict]:
        parts = Path(page.relative_path).with_suffix("").parts
        crumbs = [{"label": "Wiki", "url": "/wiki"}]
        for part in parts[:-1]:
            if part == "index":
                continue
            crumbs.append({"label": part.replace("-", " ").title(), "url": ""})
        crumbs.append({"label": page.title, "url": ""})
        return crumbs

    @classmethod
    def page_tree(cls, pages: list[WikiPage]) -> list[dict]:
        rows = []
        for page in pages:
            parts = Path(page.relative_path).parts
            group = "Home" if len(parts) == 1 else parts[0].replace("-", " ").title()
            for row in rows:
                if row["group"] == group:
                    row["pages"].append(page)
                    break
            else:
                rows.append({"group": group, "pages": [page]})
        return rows

    @classmethod
    def render_markdown(cls, body: str, known_ids: set[str] | None = None) -> tuple[str, tuple[WikiHeading, ...]]:
        known_ids = known_ids or set()
        html_parts: list[str] = []
        toc: list[WikiHeading] = []
        paragraph: list[str] = []
        list_open = False
        code_open = False
        code_lines: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph
            if paragraph:
                text = " ".join(paragraph)
                html_parts.append(f"<p>{cls.render_inline(text, known_ids)}</p>")
                paragraph = []

        def close_list() -> None:
            nonlocal list_open
            if list_open:
                html_parts.append("</ul>")
                list_open = False

        for raw_line in body.splitlines():
            line = raw_line.rstrip()
            if line.startswith("```"):
                flush_paragraph()
                close_list()
                if code_open:
                    html_parts.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                    code_lines = []
                    code_open = False
                else:
                    code_open = True
                continue

            if code_open:
                code_lines.append(line)
                continue

            if not line.strip():
                flush_paragraph()
                close_list()
                continue

            heading_match = re.match(r"^(#{2,6})\s+(.+)$", line)
            if heading_match:
                flush_paragraph()
                close_list()
                level = len(heading_match.group(1))
                text = heading_match.group(2).strip()
                anchor = cls.slugify(text)
                toc.append(WikiHeading(level=level, text=text, anchor=anchor))
                html_parts.append(f'<h{level} id="{anchor}">{cls.render_inline(text, known_ids)}</h{level}>')
                continue

            list_match = re.match(r"^-\s+(.+)$", line)
            if list_match:
                flush_paragraph()
                if not list_open:
                    html_parts.append("<ul>")
                    list_open = True
                html_parts.append(f"<li>{cls.render_list_item(list_match.group(1), known_ids)}</li>")
                continue

            paragraph.append(line.strip())

        flush_paragraph()
        close_list()
        if code_open:
            html_parts.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
        return "\n".join(html_parts), tuple(toc)

    @classmethod
    def render_list_item(cls, text: str, known_ids: set[str]) -> str:
        todo_match = re.match(r"(?i)^todo:\s*(.+)$", text.strip())
        if todo_match:
            return f'<span class="wiki-todo">todo</span> {cls.render_inline(todo_match.group(1), known_ids)}'
        return cls.render_inline(text, known_ids)

    @classmethod
    def render_inline(cls, text: str, known_ids: set[str]) -> str:
        rendered = escape(text)
        rendered = re.sub(
            r"\[\[([a-zA-Z0-9_-]+)(?:\|([^\]]+))?\]\]",
            lambda match: cls._render_wiki_link(match, known_ids),
            rendered,
        )
        rendered = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            lambda match: f'<a href="{escape(match.group(2), quote=True)}">{match.group(1)}</a>',
            rendered,
        )
        rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
        rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
        return rendered

    @staticmethod
    def validation_errors(pages: list[WikiPage]) -> list[str]:
        errors: list[str] = []
        ids: dict[str, str] = {}
        for page in pages:
            if page.id in ids:
                errors.append(f"Duplicate page id '{page.id}' in {page.relative_path} and {ids[page.id]}")
            ids[page.id] = page.relative_path
            missing = sorted(REQUIRED_METADATA - set(page.metadata))
            for field_name in missing:
                errors.append(f"{page.relative_path}: missing metadata '{field_name}'")
            if page.status not in VALID_STATUSES:
                errors.append(f"{page.relative_path}: invalid status '{page.status}'")
            if page.parent and page.parent not in ids and not any(candidate.id == page.parent for candidate in pages):
                errors.append(f"{page.relative_path}: parent '{page.parent}' was not found")
            if page.risk_level == "veterinary" or "veterinary" in page.tags:
                for field_name in sorted(VETERINARY_FIELDS - set(page.metadata)):
                    errors.append(f"{page.relative_path}: veterinary page missing '{field_name}'")
                if page.metadata.get("approval_status", "draft") != "approved" and page.status != "draft":
                    errors.append(f"{page.relative_path}: unapproved veterinary content must remain draft")
            for link_id in re.findall(r"\[\[([a-zA-Z0-9_-]+)(?:\|[^\]]+)?\]\]", page.body):
                if link_id not in {candidate.id for candidate in pages}:
                    errors.append(f"{page.relative_path}: broken wiki link '{link_id}'")
        return errors

    @classmethod
    def _parse_page(cls, path: Path) -> tuple[dict, str, Path]:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return {}, text, path
        _, frontmatter, body = text.split("---", 2)
        return cls.parse_frontmatter(frontmatter), body.strip(), path

    @staticmethod
    def parse_frontmatter(frontmatter: str) -> dict:
        metadata: dict[str, str | list[str]] = {}
        for raw_line in frontmatter.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            value = raw_value.strip()
            if value.startswith("[") and value.endswith("]"):
                metadata[key.strip()] = [
                    item.strip().strip("\"'")
                    for item in value[1:-1].split(",")
                    if item.strip()
                ]
            else:
                metadata[key.strip()] = value.strip("\"'")
        return metadata

    @classmethod
    def _build_page(
        cls,
        metadata: dict,
        body: str,
        path: Path,
        rendered_html: str,
        toc: tuple[WikiHeading, ...],
    ) -> WikiPage:
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        return WikiPage(
            id=str(metadata.get("id", cls.slugify(path.stem))),
            title=str(metadata.get("title", path.stem.replace("-", " ").title())),
            parent=str(metadata.get("parent", "")),
            tags=tuple(str(tag) for tag in tags),
            status=str(metadata.get("status", "draft")),
            risk_level=str(metadata.get("risk_level", "general")),
            page_type=str(metadata.get("type", "knowledge")),
            relative_path=path.relative_to(WIKI_ROOT).as_posix(),
            source_path=path,
            body=body,
            html=rendered_html,
            toc=toc,
            updated_at=datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            metadata=metadata,
        )

    @staticmethod
    def _render_wiki_link(match, known_ids: set[str]) -> str:
        page_id = match.group(1)
        label = match.group(2) or page_id
        if page_id in known_ids:
            return f'<a href="/wiki/{escape(page_id, quote=True)}">{escape(label)}</a>'
        return f'<span class="wiki-broken-link">{escape(label)}</span>'

    @staticmethod
    def slugify(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "section"
