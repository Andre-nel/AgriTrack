from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import CashTransaction, Farm, LivestockTrade


def _create_farm(name: str = "Cash Flow Farm") -> Farm:
    farm = Farm(name=name, timezone="SAST")
    db.session.add(farm)
    db.session.commit()
    return farm


def _transaction_form(*, farm_id: str = "", transaction_date: str = "2026-01-05", description: str = "Cash flow entry"):
    return MultiDict(
        [
            ("return_year", "2026"),
            ("return_tab", "overall"),
            ("transaction_date", transaction_date),
            ("farm_id", farm_id),
            ("reference", "EFT-1"),
            ("counterparty", "Counterparty"),
            ("description", description),
            ("line_species_scope", "Sheep"),
            ("line_category_code", "lamb_sales"),
            ("line_amount", "10000"),
            ("line_species_scope", "Sheep"),
            ("line_category_code", "feed_cost"),
            ("line_amount", "3500"),
            ("line_species_scope", ""),
            ("line_category_code", "labour"),
            ("line_amount", "700"),
        ]
    )


def _livestock_trade_form(*, farm_ids: list[str], price_per_kg: str = "10", weights_csv: str = "100, 200"):
    values = MultiDict(
        [
            ("return_year", "2026"),
            ("return_tab", "sheep"),
            ("trade_type", "sale"),
            ("trade_animal_mode", "lamb"),
            ("trade_date", "2026-02-10"),
            ("counterparty", "Abattoir Buyer"),
            ("reference", "LAMB-INV-1"),
            ("vat_rate", "15"),
            ("breed", "Dorper"),
            ("sex", "mixed"),
            ("age_class", "lamb"),
            ("price_per_kg", price_per_kg),
            ("weights_csv", weights_csv),
            ("notes", "February lamb sale"),
        ]
    )
    for farm_id in farm_ids:
        values.add("farm_ids", farm_id)
    return values


def test_cash_flow_page_loads_and_nav_contains_link(client):
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b"Cash Flow" in dashboard.data

    response = client.get("/cash-flow")
    assert response.status_code == 200
    assert b"Cash Flow Dashboard" in response.data
    assert b"Overall Business Cash Flow" in response.data
    assert b"/cash-flow/livestock-trades/new" in response.data
    assert b"Save Livestock Trade" not in response.data

    new_trade_page = client.get("/cash-flow/livestock-trades/new?return_year=2026&return_tab=sheep")
    assert new_trade_page.status_code == 200
    assert b"Record Livestock Trade" in new_trade_page.data
    assert b"Save Livestock Trade" in new_trade_page.data
    assert b'name="return_year" value="2026"' in new_trade_page.data


def test_cash_flow_create_edit_delete_and_year_filter(client, app):
    with app.app_context():
        farm = _create_farm()
        farm_id = str(farm.id)

    create_response = client.post(
        "/cash-flow/transactions",
        data=_transaction_form(farm_id=farm_id),
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    create_body = create_response.data.decode("utf-8")
    assert "Cash transaction recorded" in create_body
    assert "Labour" in create_body

    with app.app_context():
        transaction = CashTransaction.query.first()
        assert transaction is not None
        transaction_id = str(transaction.id)
        assert len(transaction.lines) == 3

    sheep_page = client.get("/cash-flow?year=2026&tab=sheep")
    sheep_body = sheep_page.data.decode("utf-8")
    assert sheep_page.status_code == 200
    assert "Sheep Cash Flow" in sheep_body
    assert "R10 000,00" in sheep_body
    assert "R3 500,00" in sheep_body

    edit_form = MultiDict(
        [
            ("return_year", "2026"),
            ("return_tab", "sheep"),
            ("transaction_date", "2026-01-05"),
            ("farm_id", farm_id),
            ("reference", "EFT-2"),
            ("counterparty", "Updated Counterparty"),
            ("description", "Updated cash flow entry"),
            ("line_species_scope", "Sheep"),
            ("line_category_code", "lamb_sales"),
            ("line_amount", "12000"),
            ("line_species_scope", "Sheep"),
            ("line_category_code", "feed_cost"),
            ("line_amount", "4000"),
            ("line_species_scope", ""),
            ("line_category_code", "labour"),
            ("line_amount", "900"),
        ]
    )
    edit_response = client.post(
        f"/cash-flow/transactions/{transaction_id}/edit",
        data=edit_form,
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    edit_body = edit_response.data.decode("utf-8")
    assert "Cash transaction updated" in edit_body
    assert "Updated cash flow entry" in edit_body

    updated_sheep_page = client.get("/cash-flow?year=2026&tab=sheep")
    updated_sheep_body = updated_sheep_page.data.decode("utf-8")
    assert "R12 000,00" in updated_sheep_body
    assert "R4 000,00" in updated_sheep_body

    past_year_form = MultiDict(
        [
            ("return_year", "2025"),
            ("return_tab", "overall"),
            ("transaction_date", "2025-12-20"),
            ("farm_id", ""),
            ("reference", "OLD-1"),
            ("counterparty", "Old buyer"),
            ("description", "Old season entry"),
            ("line_species_scope", "Goat"),
            ("line_category_code", "kid_sales"),
            ("line_amount", "2200"),
        ]
    )
    past_year_response = client.post(
        "/cash-flow/transactions",
        data=past_year_form,
        follow_redirects=True,
    )
    assert past_year_response.status_code == 200

    current_year_page = client.get("/cash-flow?year=2026&tab=overall")
    current_year_body = current_year_page.data.decode("utf-8")
    assert "Updated cash flow entry" in current_year_body
    assert "Old season entry" not in current_year_body

    delete_response = client.post(
        f"/cash-flow/transactions/{transaction_id}/delete",
        data={"return_year": "2026", "return_tab": "overall"},
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    delete_body = delete_response.data.decode("utf-8")
    assert "Cash transaction deleted" in delete_body

    with app.app_context():
        assert CashTransaction.query.filter_by(id=transaction_id).first() is None


def test_livestock_trade_web_flow_syncs_cash_flow_and_allows_edit_delete(client, app):
    with app.app_context():
        first = _create_farm("Livestock Trade Farm A")
        second = _create_farm("Livestock Trade Farm B")
        farm_ids = [str(first.id), str(second.id)]

    create_response = client.post(
        "/cash-flow/livestock-trades",
        data=_livestock_trade_form(farm_ids=farm_ids),
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    create_body = create_response.data.decode("utf-8")
    assert "Livestock trade recorded" in create_body
    assert "Average Weight" in create_body
    assert "Total Excl VAT" in create_body
    assert "R3 000,00" in create_body

    with app.app_context():
        trade = LivestockTrade.query.first()
        assert trade is not None
        trade_id = str(trade.id)
        transaction_id = str(trade.cash_transaction_id)
        assert trade.cash_transaction.farm_id is None
        assert trade.cash_transaction.lines[0].category_code == "lamb_sales"

    sheep_page = client.get("/cash-flow?year=2026&tab=sheep")
    assert sheep_page.status_code == 200
    sheep_body = sheep_page.data.decode("utf-8")
    assert "R3 450,00" in sheep_body
    assert "Abattoir Buyer" in sheep_body

    edit_response = client.post(
        f"/cash-flow/livestock-trades/{trade_id}/edit",
        data=_livestock_trade_form(farm_ids=farm_ids[:1], price_per_kg="12", weights_csv="100, 200"),
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    edit_body = edit_response.data.decode("utf-8")
    assert "Livestock trade updated" in edit_body
    assert "R3 600,00" in edit_body

    with app.app_context():
        trade = LivestockTrade.query.filter_by(id=trade_id).one()
        assert trade.cash_transaction.farm_id == farm_ids[0]
        assert str(trade.cash_transaction_id) == transaction_id

    delete_response = client.post(
        f"/cash-flow/livestock-trades/{trade_id}/delete",
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert "Livestock trade deleted" in delete_response.data.decode("utf-8")

    with app.app_context():
        assert LivestockTrade.query.filter_by(id=trade_id).first() is None
        assert CashTransaction.query.filter_by(id=transaction_id).first() is None


def test_generated_livestock_trade_cash_transaction_cannot_be_manually_edited_or_deleted(client, app):
    with app.app_context():
        farm = _create_farm("Managed Livestock Transaction Farm")
        farm_id = str(farm.id)

    create_response = client.post(
        "/cash-flow/livestock-trades",
        data=_livestock_trade_form(farm_ids=[farm_id]),
        follow_redirects=True,
    )
    assert create_response.status_code == 200

    with app.app_context():
        trade = LivestockTrade.query.first()
        trade_id = str(trade.id)
        transaction_id = str(trade.cash_transaction_id)

    edit_response = client.post(
        f"/cash-flow/transactions/{transaction_id}/edit",
        data=_transaction_form(farm_id=farm_id),
        follow_redirects=True,
    )
    assert edit_response.status_code == 200
    edit_body = edit_response.data.decode("utf-8")
    assert "managed from the livestock trade detail page" in edit_body
    assert "Sale Lambs" in edit_body

    delete_response = client.post(
        f"/cash-flow/transactions/{transaction_id}/delete",
        data={"return_year": "2026", "return_tab": "sheep"},
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert "managed from the livestock trade detail page" in delete_response.data.decode("utf-8")

    with app.app_context():
        assert LivestockTrade.query.filter_by(id=trade_id).first() is not None
        assert CashTransaction.query.filter_by(id=transaction_id).first() is not None
