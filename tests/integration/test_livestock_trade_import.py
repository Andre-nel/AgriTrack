import copy
import html
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, Farm, LivestockTrade, LivestockTradeGroup, LivestockTradeWeight
from app.services.livestock_trade_calculations import payload_statistics
from app.services.livestock_trade_payload import load_document, normalize_document
from app.services.livestock_trade_service import LivestockTradeService

BASE = "/cash-flow/livestock-trades"


def document():
    return {
        "schema_version": 1, "import_id": str(uuid4()), "trade_type": "sale", "date": "2026-10-15",
        "counterparty": "Test Buyer", "species": "Cattle", "pricing_model": "weight", "vat_percent": 15,
        "notes": "Confirmed weights", "groups": [
            {"label": "Heifers", "breed": "Bonsmara", "sex": "female", "age_class": "young", "weights_kg": [250, 260], "price_per_kg": 40},
            {"label": "Bull calves", "breed": "Bonsmara", "sex": "male", "age_class": "calf", "weights_kg": [280, 300], "price_per_kg": 45},
        ],
    }


@pytest.fixture
def farm_id(app):
    with app.app_context():
        farm = Farm(name="Trade import farm", active=True)
        db.session.add(farm)
        db.session.commit()
        return farm.id


def form(doc, farm_id, token=None):
    result = {key: value for key, value in doc.items() if key not in ("schema_version", "groups") and value is not None}
    result.update(group_count=str(len(doc["groups"])), farm_ids=[farm_id])
    for index, group in enumerate(doc["groups"]):
        for key, value in group.items():
            if key == "weights_kg":
                value = "\n".join(str(weight) if weight is not None else "null" for weight in value)
            result[f"group_{index}_{key}"] = "" if value is None else value
    if token:
        result["preview_token"] = token
    return result


def token_from(response):
    match = re.search(r'name="preview_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, response.get_data(as_text=True)
    return html.unescape(match[1])


def test_large_pasted_document_preview_and_changed_import_identity(client, app, farm_id):
    doc = document()
    # Formatting alone can exceed Werkzeug's default 500 KB form-field limit.
    raw = json.dumps(doc) + " " * 510_000
    response = client.post(BASE + "/import/preview", data={"trade_json": raw, "farm_ids": farm_id})
    assert response.status_code == 200
    token = token_from(response)
    doc["import_id"] = str(uuid4())
    response = client.post(BASE + "/import/confirm", data=form(doc, farm_id, token))
    assert response.status_code == 400
    assert "details changed" in response.get_data(as_text=True)
    with app.app_context():
        assert LivestockTrade.query.count() == 0
        assert CashTransaction.query.count() == 0


def test_group_sex_and_combined_statistics_and_zero_vat(app, farm_id):
    with app.app_context():
        doc = document()
        payload = normalize_document(doc, [farm_id])
        stats = payload_statistics(payload)
        assert stats["stats"]["count"] == 4
        assert stats["stats"]["total_weight"] == Decimal("1090.000")
        assert stats["stats"]["average_weight"] == Decimal("272.500")
        assert stats["stats"]["unit_price"] == Decimal("42.6606")
        assert stats["stats"]["average_price_per_animal"] == Decimal("11625.00")
        assert stats["stats"]["base_total"] == Decimal("46500.00")
        assert stats["stats"]["gross_total"] == Decimal("53475.00")
        doc["groups"].append({**doc["groups"][0], "weights_kg": [200], "price_per_kg": 42})
        doc["groups"].append({**doc["groups"][0], "weights_kg": [100], "sex": "mixed"})
        doc["vat_percent"] = 0
        stats = payload_statistics(normalize_document(doc, [farm_id]))
        assert stats["sex_stats"]["cow"]["count"] == 3
        assert stats["sex_stats"]["cow"]["total_weight"] == 710
        assert stats["sex_stats"]["cow"]["base_total"] == 28800
        assert stats["sex_stats"]["mixed"]["count"] == 1
        assert stats["stats"]["gross_total"] == stats["stats"]["base_total"] == 58900


def test_vat_rounding_sums_group_amounts(app, farm_id):
    with app.app_context():
        doc = document()
        doc["vat_percent"] = 20
        for group in doc["groups"]:
            group.update(weights_kg=[1], price_per_kg="0.03")
        stats = payload_statistics(normalize_document(doc, [farm_id]))["stats"]
        assert stats["base_total"] == Decimal("0.06")
        assert stats["vat_amount"] == Decimal("0.02")
        assert stats["gross_total"] == Decimal("0.08")


@pytest.mark.parametrize("species,age", [("Cattle", "calf"), ("Sheep", "lamb"), ("Goat", "kid")])
@pytest.mark.parametrize("pricing", ["weight", "head"])
def test_all_species_allow_either_pricing_method(app, farm_id, species, age, pricing):
    with app.app_context():
        doc = document()
        doc.update(species=species, pricing_model=pricing, trade_type="purchase")
        for group in doc["groups"]:
            group["age_class"] = age
            if pricing == "head":
                group.pop("weights_kg")
                group.pop("price_per_kg")
                group.update(head_count=3, price_per_head=1000, hair_lengths="long")
        trade, created = LivestockTradeService.import_document(doc, [farm_id])
        assert created
        stats = LivestockTradeService.trade_statistics(trade)
        assert trade.cash_transaction.lines[0].direction == "outflow"
        if pricing == "head":
            assert stats["count"] == 6
            assert stats["total_weight"] is None
            assert stats["average_weight"] is None
            assert stats["gross_total"] == 6900


@pytest.mark.parametrize("bad", [None, True, False, 0, -1, "", "null", "NaN", "Infinity", "1e100", "0.0001"])
def test_bad_weights_are_never_dropped(app, farm_id, bad):
    with app.app_context():
        doc = document()
        doc["groups"][1]["weights_kg"][0] = bad
        with pytest.raises(ValueError, match="Group 2: Weight 1"):
            normalize_document(doc, [farm_id])
        assert AnimalGroupType.query.count() == 0


@pytest.mark.parametrize("raw", ['{', '[]', '{"schema_version":2,"groups":[{}]}', '{"schema_version":true,"groups":[{}]}', '{"schema_version":1,"schema_version":1,"groups":[{}]}', '{"schema_version":1,"groups":[{}],"extra":1}', '{"schema_version":1,"groups":[{"price_per_kg":NaN}]}'])
def test_invalid_json_and_schema(raw):
    with pytest.raises(ValueError):
        load_document(raw)


def test_mixed_species_methods_and_conflicting_type_rejected(app, farm_id):
    with app.app_context():
        doc = document()
        doc["groups"][0]["species"] = "Sheep"
        with pytest.raises(ValueError, match="Species and pricing method"):
            normalize_document(doc, [farm_id])
        doc["groups"][0].pop("species")
        doc["groups"][0]["price_per_head"] = 3000
        with pytest.raises(ValueError, match="per-head"):
            normalize_document(doc, [farm_id])
        doc["groups"][0].pop("price_per_head")
        existing = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="bul", age_class="young")
        db.session.add(existing)
        db.session.commit()
        doc["groups"][0]["animal_group_type_id"] = existing.id
        with pytest.raises(ValueError, match="Sex conflicts"):
            normalize_document(doc, [farm_id])


def test_preview_paste_and_upload_are_read_only_and_allow_missing_metadata(client, app, farm_id):
    doc = document()
    doc["groups"][0]["breed"] = None
    doc["groups"][0]["sex"] = None
    doc["groups"][0]["age_class"] = None
    for data in (
        {"trade_json": json.dumps(doc)},
        {"trade_file": (io.BytesIO(json.dumps(doc).encode()), "trade.json")},
    ):
        response = client.post(BASE + "/import/preview", data=data)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Calculated preview" in body
        assert re.findall(r"<tr><th>(.*?)</th><td>", body) == ["Heifers", "Bull calves", "Combined trade"]
        assert 'name="preview_token"' not in body
        assert 'name="group_0_weights_kg"' in body
    with app.app_context():
        assert AnimalGroupType.query.count() == 0
        assert CashTransaction.query.count() == LivestockTrade.query.count() == 0


def test_web_preview_confirm_retry_edit_delete_and_cashflow(client, app, farm_id):
    doc = document()
    response = client.post(BASE + "/import/preview", data=form(doc, farm_id))
    assert response.status_code == 200
    preview = response.get_data(as_text=True)
    assert re.findall(r"<tr><th>(.*?)</th><td>", preview) == ["Heifers", "Bull calves", "Combined trade"]
    assert "R53 475,00" in preview
    token = token_from(response)
    confirmed = client.post(BASE + "/import/confirm", data=form(doc, farm_id, token))
    assert confirmed.status_code == 302
    detail_url = confirmed.headers["Location"]
    detail = client.get(detail_url).get_data(as_text=True)
    assert "Sex: female" in detail and "Sex: male" in detail
    assert "Combined trade" in detail and "R53 475,00" in detail
    retry = client.post(BASE + "/import/confirm", data=form(doc, farm_id, token))
    assert retry.headers["Location"] == detail_url
    with app.app_context():
        trade = LivestockTrade.query.one()
        trade_id, cash_id = trade.id, trade.cash_transaction_id
        assert len(trade.groups) == 2
        assert CashTransaction.query.count() == 1
        assert trade.cash_transaction.transaction_date == date(2026, 10, 15)
    cash = client.get("/cash-flow?year=2026&tab=cattle").get_data(as_text=True)
    assert "R53 475,00" in cash
    doc["groups"][0]["price_per_kg"] = 41
    edited = client.post(detail_url + "/edit", data=form(doc, farm_id), follow_redirects=True)
    assert edited.status_code == 200
    with app.app_context():
        trade = db.session.get(LivestockTrade, trade_id)
        assert trade.cash_transaction_id == cash_id
        assert trade.cash_transaction.lines[0].amount == Decimal("54061.50")
    client.post(detail_url + "/delete")
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0
        assert LivestockTradeGroup.query.count() == LivestockTradeWeight.query.count() == 0


def test_changed_preview_and_same_id_different_payload_require_review(client, app, farm_id):
    doc = document()
    token = token_from(client.post(BASE + "/import/preview", data=form(doc, farm_id)))
    doc["counterparty"] = "Changed buyer"
    response = client.post(BASE + "/import/confirm", data=form(doc, farm_id, token))
    assert response.status_code == 400
    assert "details changed" in response.get_data(as_text=True)
    token = token_from(response)
    assert client.post(BASE + "/import/confirm", data=form(doc, farm_id, token)).status_code == 302
    doc["counterparty"] = "Different trade"
    token = token_from(client.post(BASE + "/import/preview", data=form(doc, farm_id)))
    response = client.post(BASE + "/import/confirm", data=form(doc, farm_id, token))
    assert response.status_code == 400
    assert "Edit the existing trade" in response.get_data(as_text=True)
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 1


def test_failed_confirmation_rolls_back_new_types_and_cashflow(app, farm_id, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("simulated posting failure")

    with app.app_context():
        monkeypatch.setattr(LivestockTradeService, "sync_cash_transaction", fail)
        with pytest.raises(ValueError, match="posting failure"):
            LivestockTradeService.import_document(document(), [farm_id])
        assert AnimalGroupType.query.count() == CashTransaction.query.count() == LivestockTrade.query.count() == 0


def test_210_weights_survive_render_and_metadata_edit(client, app, farm_id):
    doc = document()
    doc["groups"] = [doc["groups"][0]]
    doc["groups"][0]["weights_kg"] = [str(Decimal(20) + Decimal(index) / 10) for index in range(210)]
    with app.app_context():
        trade, _ = LivestockTradeService.import_document(doc, [farm_id])
        trade_id = trade.id
    detail = client.get(BASE + "/" + trade_id).get_data(as_text=True)
    match = re.search(r'<textarea name="group_0_weights_kg"[^>]*>(.*?)</textarea>', detail, re.S)
    assert match
    weights = html.unescape(match[1]).split()
    assert len(weights) == 210
    edited_form = form(doc, farm_id)
    edited_form["group_0_weights_kg"] = match[1]
    edited_form["notes"] = "Metadata-only change"
    assert client.post(BASE + "/" + trade_id + "/edit", data=edited_form).status_code == 302
    with app.app_context():
        trade = db.session.get(LivestockTrade, trade_id)
        stats = LivestockTradeService.trade_statistics(trade)
        assert stats["count"] == 210
        assert stats["total_weight"] == Decimal("6394.500")
        assert CashTransaction.query.count() == 1


def test_missing_metadata_failed_save_and_legacy_update_cannot_drop_groups(app, farm_id):
    with app.app_context():
        doc = document()
        incomplete = copy.deepcopy(doc)
        incomplete["groups"][1]["sex"] = None
        with pytest.raises(ValueError, match="Group 2: Sex"):
            LivestockTradeService.import_document(incomplete, [farm_id])
        assert AnimalGroupType.query.count() == 0
        trade, _ = LivestockTradeService.import_document(doc, [farm_id])
        with pytest.raises(ValueError, match="grouped editor"):
            LivestockTradeService.update_trade_from_form(trade, {})
        assert len(trade.groups) == 2


def test_import_routes_require_login_and_template_ids_are_fresh(client, app):
    first = client.get(BASE + "/template")
    second = client.get(BASE + "/template")
    assert first.json["import_id"] != second.json["import_id"]
    assert first.headers["Cache-Control"] == "no-store"
    app.config["WEB_AUTH_REQUIRED"] = True
    for suffix in ("/import", "/template", "/import-instructions"):
        assert client.get(BASE + suffix).status_code == 302
    assert client.post(BASE + "/import/confirm", data={}).status_code == 302


def test_concurrent_import_commits_one_trade_and_cash_transaction(tmp_path):
    class ConcurrentConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "concurrent.sqlite").as_posix()

    application = create_app(ConcurrentConfig)
    with application.app_context():
        db.create_all()
        farm = Farm(name="Concurrent farm")
        db.session.add(farm)
        db.session.commit()
        farm_id = farm.id
    doc = document()
    barrier = Barrier(2)

    def run_import():
        with application.app_context():
            barrier.wait(timeout=10)
            trade, created = LivestockTradeService.import_document(copy.deepcopy(doc), [farm_id])
            return trade.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_import(), range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    with application.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 1
        assert AnimalGroupType.query.count() == 2
        db.session.remove()
        db.engine.dispose()
