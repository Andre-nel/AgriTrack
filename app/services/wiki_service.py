from dataclasses import dataclass
import re

from sqlalchemy import or_

from app.extensions import db
from app.models import WikiArticle


@dataclass(frozen=True)
class SeedWikiArticle:
    slug: str
    title: str
    category: str
    summary: str
    facts: tuple[str, ...]
    todos: tuple[str, ...]


DEFAULT_WIKI_ARTICLES: tuple[SeedWikiArticle, ...] = (
    SeedWikiArticle(
        slug="lambs-dose",
        title="Lambs Dose",
        category="Sheep",
        summary=(
            "Dose lambs with Lintex first, then First Drench later. Lintex kills some worms "
            "that First Drench does not cover, and it is lighter on the lambs' stomachs."
        ),
        facts=(
            "First treatment: Lintex.",
            "Later treatment: First Drench.",
            "Reason for sequence: Lintex covers some worms not covered by First Drench.",
            "Handling note: Lintex is lighter on lamb stomachs.",
        ),
        todos=(
            "Confirm lamb age or weight window for the first Lintex dose.",
            "Record dosage rates for Lintex and First Drench.",
            "Record repeat interval and withdrawal periods from the product labels.",
        ),
    ),
    SeedWikiArticle(
        slug="ticks",
        title="Ticks",
        category="Animal Health",
        summary=(
            "Run voetbad en gans once a month. Use Cyphermethrin and opblaas olie at "
            "2 litres on 18 litres."
        ),
        facts=(
            "Frequency: once a month.",
            "Method note: voetbad en gans.",
            "Mix note: Cyphermethrin and opblaas olie, 2 litres on 18 litres.",
        ),
        todos=(
            "Confirm exact product name and active ingredient spelling from the label.",
            "Record whether the 2 litres is total product, oil, or mixed concentrate.",
            "Record withholding periods, safety handling, and which animals should be treated.",
        ),
    ),
    SeedWikiArticle(
        slug="goats-swollen-stomachs",
        title="Goats: Swollen Stomachs",
        category="Goats",
        summary="For goats with swollen stomachs, use First Drench and Ivermax.",
        facts=(
            "Products noted: First Drench and Ivermax.",
            "Use case noted: swollen stomachs in goats.",
        ),
        todos=(
            "Record diagnosis signs that separate swollen stomachs from emergency bloat.",
            "Record dosage rates, repeat timing, and withdrawal periods.",
            "Record when to call the vet or separate the animal for closer monitoring.",
        ),
    ),
    SeedWikiArticle(
        slug="goats-bloednier",
        title="Goats: Bloednier",
        category="Goats",
        summary="Use Multivax P Plus for goats against Bloednier.",
        facts=("Product noted: Multivax P Plus.",),
        todos=(
            "Record vaccination age, booster schedule, dosage, and route.",
            "Record storage requirements and expiry checks for opened bottles.",
            "Record whether pregnant ewes, kids, or replacement animals follow different timing.",
        ),
    ),
    SeedWikiArticle(
        slug="skape-bloutong",
        title="Skape Bloutong",
        category="Sheep",
        summary="Bloutong goed requires three different injections, spaced a couple of weeks apart.",
        facts=(
            "Three different injections are needed.",
            "Applications are spaced a couple of weeks apart.",
        ),
        todos=(
            "Product name.",
            "Exact durations between applications.",
            "Dosage.",
        ),
    ),
    SeedWikiArticle(
        slug="lambing-goats",
        title="Lambing Goats",
        category="Goats",
        summary=(
            "Use small camps for lambing goats, with a goat shed in each camp that is big enough "
            "for all goats in that camp. Put pills in feed troughs and feed corn each morning. "
            "Mark goats with paint before lambing, then mark the little goat the same as the mother "
            "after lambing so the mother can be identified if there are problems."
        ),
        facts=(
            "Use small camps for lambing groups.",
            "Each camp needs a goat shed big enough for all goats in the camp.",
            "Put pills in feed troughs.",
            "Feed corn each morning.",
            "Mark goats with paint before they start lambing.",
            "After lambing, mark the little goat the same as the mother.",
            "The matching paint marks help identify the mother if there are problems.",
        ),
        todos=(
            "Record maximum goats per camp and minimum shed space per goat.",
            "Record pill product name, dosage, and when to stop giving it.",
            "Record paint colour system and how often marks must be refreshed.",
            "Record lambing check cadence and problem signs that need action.",
        ),
    ),
)


class WikiService:
    MAX_TITLE_LENGTH = 160
    MAX_CATEGORY_LENGTH = 80
    MAX_SUMMARY_LENGTH = 2000
    MAX_BODY_LENGTH = 12000

    @staticmethod
    def lines_from_text(value: str | None) -> list[str]:
        lines = []
        for raw_line in (value or "").splitlines():
            line = raw_line.strip()
            if line.startswith("- "):
                line = line[2:].strip()
            if line:
                lines.append(line)
        return lines

    @classmethod
    def ensure_default_articles(cls) -> int:
        if WikiArticle.query.first():
            return 0

        created_count = 0
        for seed in DEFAULT_WIKI_ARTICLES:
            db.session.add(
                WikiArticle(
                    slug=seed.slug,
                    title=seed.title,
                    category=seed.category,
                    summary=seed.summary,
                    content="\n".join(seed.facts),
                    todos="\n".join(seed.todos),
                )
            )
            created_count += 1
        if created_count:
            db.session.commit()
        return created_count

    @classmethod
    def create_article(
        cls,
        *,
        title: str | None,
        category: str | None,
        summary: str | None,
        content: str | None,
        todos: str | None,
    ) -> WikiArticle:
        article = WikiArticle(
            slug=cls.unique_slug(cls.slugify(title)),
            title=cls.require_text(title, "Title", cls.MAX_TITLE_LENGTH),
            category=cls.optional_text(category, "Category", cls.MAX_CATEGORY_LENGTH, default="General"),
            summary=cls.require_text(summary, "Summary", cls.MAX_SUMMARY_LENGTH),
            content=cls.optional_text(content, "Known info", cls.MAX_BODY_LENGTH),
            todos=cls.optional_text(todos, "Missing info", cls.MAX_BODY_LENGTH),
        )
        db.session.add(article)
        return article

    @classmethod
    def update_article(
        cls,
        article: WikiArticle,
        *,
        title: str | None,
        category: str | None,
        summary: str | None,
        content: str | None,
        todos: str | None,
    ) -> WikiArticle:
        article.title = cls.require_text(title, "Title", cls.MAX_TITLE_LENGTH)
        article.category = cls.optional_text(category, "Category", cls.MAX_CATEGORY_LENGTH, default="General")
        article.summary = cls.require_text(summary, "Summary", cls.MAX_SUMMARY_LENGTH)
        article.content = cls.optional_text(content, "Known info", cls.MAX_BODY_LENGTH)
        article.todos = cls.optional_text(todos, "Missing info", cls.MAX_BODY_LENGTH)
        return article

    @classmethod
    def search_articles(cls, *, query: str | None = None) -> list[WikiArticle]:
        article_query = WikiArticle.query
        normalized_query = (query or "").strip()
        if normalized_query:
            like = f"%{normalized_query}%"
            article_query = article_query.filter(
                or_(
                    WikiArticle.title.ilike(like),
                    WikiArticle.category.ilike(like),
                    WikiArticle.summary.ilike(like),
                    WikiArticle.content.ilike(like),
                    WikiArticle.todos.ilike(like),
                )
            )
        return article_query.order_by(WikiArticle.category.asc(), WikiArticle.title.asc()).all()

    @classmethod
    def unique_slug(cls, base_slug: str) -> str:
        candidate = base_slug
        counter = 2
        while WikiArticle.query.filter_by(slug=candidate).first():
            candidate = f"{base_slug}-{counter}"
            counter += 1
        return candidate

    @staticmethod
    def slugify(value: str | None) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
        return slug or "wiki-section"

    @staticmethod
    def require_text(value: str | None, field_name: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        if len(text) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return text

    @staticmethod
    def optional_text(
        value: str | None,
        field_name: str,
        max_length: int,
        *,
        default: str = "",
    ) -> str:
        text = (value or "").strip() or default
        if len(text) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return text
