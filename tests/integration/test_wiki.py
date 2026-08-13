from app.models import WikiArticle


def test_dashboard_renders_wiki_nav(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/wiki"' in response.data
    assert b">Wiki<" in response.data


def test_wiki_page_loads_seed_farm_knowledge(client):
    response = client.get("/wiki")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Farm Wiki" in body
    assert "Lambs Dose" in body
    assert "Lintex" in body
    assert "First Drench" in body
    assert "Ticks" in body
    assert "Cyphermethrin" in body
    assert "Goats: Swollen Stomachs" in body
    assert "Multivax P Plus" in body
    assert "Skape Bloutong" in body
    assert "Lambing Goats" in body
    assert '<span class="wiki-todo">todo</span> Product name.' in body


def test_wiki_detail_can_edit_seed_section(client, app):
    response = client.get("/wiki/lambs-dose")
    assert response.status_code == 200
    assert b"Edit Section" in response.data

    response = client.post(
        "/wiki/lambs-dose/edit",
        data={
            "title": "Lambs Dose",
            "category": "Sheep",
            "summary": "Updated lamb dose note.",
            "content": "Lintex first.\nFirst Drench later.",
            "todos": "Confirm dose by weight.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Updated lamb dose note." in body
    assert '<span class="wiki-todo">todo</span> Confirm dose by weight.' in body

    with app.app_context():
        article = WikiArticle.query.filter_by(slug="lambs-dose").one()
        assert article.summary == "Updated lamb dose note."
        assert article.todos == "Confirm dose by weight."


def test_wiki_can_create_search_and_delete_section(client, app):
    response = client.post(
        "/wiki/articles",
        data={
            "title": "Colostrum Checks",
            "category": "Lambing",
            "summary": "Check weak newborns early.",
            "content": "Watch the first feed.\nSeparate cold newborns for attention.",
            "todos": "Record exact colostrum volume.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Colostrum Checks" in body
    assert "Watch the first feed." in body
    assert '<span class="wiki-todo">todo</span> Record exact colostrum volume.' in body

    search_response = client.get("/wiki?q=colostrum")
    assert search_response.status_code == 200
    assert "Colostrum Checks" in search_response.data.decode("utf-8")

    with app.app_context():
        article = WikiArticle.query.filter_by(slug="colostrum-checks").one()
        article_id = article.id

    delete_response = client.post("/wiki/colostrum-checks/delete", follow_redirects=True)
    assert delete_response.status_code == 200
    assert b"Wiki section deleted" in delete_response.data

    with app.app_context():
        assert WikiArticle.query.filter_by(id=article_id).first() is None
