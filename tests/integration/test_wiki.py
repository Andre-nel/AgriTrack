from app.services.wiki_service import WikiService


def test_dashboard_renders_wiki_nav(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/wiki"' in response.data
    assert b">Wiki<" in response.data


def test_wiki_page_loads_markdown_farm_knowledge(client):
    response = client.get("/wiki")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Farm Wiki" in body
    assert "Git-backed farm knowledge pages" in body
    assert "Lambs Dose" in body
    assert "Ticks" in body
    assert "Goats: Swollen Stomachs" in body
    assert "Multivax P Plus" in body
    assert "Skape Bloutong" in body
    assert "Lambing Goats" in body
    assert "docs/wiki/protocols/dosing/lambs-dose.md" in body
    assert "Create Wiki Section" not in body
    assert "Edit Section" not in body


def test_wiki_detail_renders_metadata_toc_links_and_todo(client):
    response = client.get("/wiki/skape-bloutong")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Skape Bloutong" in body
    assert "Metadata" in body
    assert "Contents" in body
    assert "draft" in body
    assert "veterinary" in body
    assert "protocols/vaccination/skape-bloutong.md" in body
    assert '<span class="wiki-todo">todo</span> Product name.' in body
    assert "<h2><span class=\"wiki-todo\">todo</span> Validation</h2>" not in body
    assert "Edit Section" not in body


def test_wiki_searches_markdown_body_and_metadata(client):
    response = client.get("/wiki?q=cyphermethrin")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Ticks" in body
    assert "Lambs Dose" not in body


def test_wiki_markdown_renderer_escapes_raw_html():
    rendered, _ = WikiService.render_markdown("<script>alert('x')</script>", set())
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_wiki_metadata_validator_accepts_seed_docs():
    pages = WikiService.load_pages()
    assert WikiService.validation_errors(pages) == []
