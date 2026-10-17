import base64
import copy
import html
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from flask import request
from itsdangerous import TimestampSigner
from werkzeug.datastructures import FileStorage

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, Farm, LivestockTrade
from app.models import LivestockTradeAttachment
from app.services.livestock_trade_attachment_service import LivestockTradeAttachmentService
from app.services.livestock_trade_service import LivestockTradeService


BASE = "/cash-flow/livestock-trades"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGPcEqXBwMDAxAAGAA8+ATocNFacAAAAAElFTkSuQmCC"
)


@pytest.fixture(autouse=True)
def isolated_image_storage(app, tmp_path, monkeypatch):
    # Nested UUID directories otherwise exceed Windows MAX_PATH in long checkouts.
    with TemporaryDirectory(prefix="img-", dir=tmp_path.parent) as directory:
        instance = Path(directory)
        monkeypatch.setattr(app, "instance_path", str(instance))
        yield instance


@pytest.fixture
def farm_id(app):
    with app.app_context():
        farm = Farm(name="Image import farm", active=True)
        db.session.add(farm)
        db.session.commit()
        return farm.id


def document():
    return {
        "schema_version": 1,
        "import_id": str(uuid4()),
        "trade_type": "sale",
        "date": "2026-10-15",
        "counterparty": "Image reference buyer",
        "species": "Cattle",
        "pricing_model": "weight",
        "vat_percent": 15,
        "groups": [{
            "label": "Heifers", "breed": "Bonsmara", "sex": "female",
            "age_class": "young", "weights_kg": [250, 260], "price_per_kg": 40,
        }],
    }


def form(doc, farm_id, *, token=None, images=()):
    data = {
        key: value for key, value in doc.items()
        if key not in ("schema_version", "groups") and value is not None
    }
    data.update(group_count=str(len(doc["groups"])), farm_ids=[farm_id])
    for index, group in enumerate(doc["groups"]):
        for key, value in group.items():
            if key == "weights_kg":
                value = "\n".join(str(weight) for weight in value)
            data[f"group_{index}_{key}"] = value
    if token:
        data["preview_token"] = token
    data["attachment_tokens"] = list(images)
    return data


def form_body(response, *, action=None):
    body = response.get_data(as_text=True)
    for attributes, fields in re.findall(r"<form\b([^>]*)>(.*?)</form>", body, re.S):
        if action is not None:
            matches = f'action="{action}"' in attributes
        else:
            matches = "data-grouped-trade-form" in attributes or f'action="{BASE}/import/preview"' in attributes
        if matches:
            return fields
    pytest.fail("The requested trade form was not found in the response")


def hidden_values(response, name):
    values = re.findall(
        rf'name="{re.escape(name)}" value="([^"]*)"', form_body(response)
    )
    return [html.unescape(value) for value in values]


def preview_token(response):
    values = hidden_values(response, "preview_token")
    assert len(values) == 1, response.get_data(as_text=True)
    return values[0]


def upload(name="weights.png", content=PNG, mimetype="image/png"):
    return io.BytesIO(content), name, mimetype


def initial_preview(client, doc, farm_id, *, files=None):
    data = {"trade_json": json.dumps(doc), "farm_ids": [farm_id]}
    if files is not None:
        data["trade_images"] = files
    response = client.post(BASE + "/import/preview", data=data)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def staged_url(token):
    return BASE + "/import/images?token=" + quote(token, safe="")


def get_image(client, url):
    response = client.get(url)
    try:
        # Buffer the file body and close its handle before any assertion can fail.
        response.get_data()
    finally:
        response.close()
    return response


def image_files(instance):
    return sorted(path for path in Path(instance).rglob("*") if path.is_file())


def test_standalone_id_generator_does_not_create_trade(client, app):
    first = client.get(BASE + "/import-id")
    second = client.get(BASE + "/import-id")
    assert first.status_code == second.status_code == 200
    assert UUID(first.json["import_id"]).version == 4
    assert first.json["import_id"] != second.json["import_id"]
    assert first.headers["Cache-Control"] == "no-store"
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0
    app.config["WEB_AUTH_REQUIRED"] = True
    assert client.get(BASE + "/import-id").status_code == 302


@pytest.mark.parametrize("chunked", [False, True])
def test_operator_request_limit_is_preserved(client, app, chunked):
    app.config["MAX_CONTENT_LENGTH"] = 256
    overrides = {"CONTENT_LENGTH": "", "wsgi.input_terminated": True} if chunked else {}
    response = client.post(BASE + "/import/preview", data={
        "trade_json": json.dumps(document()), "trade_images": [upload()],
    }, environ_overrides=overrides)
    assert response.status_code == 413


def test_route_body_limit_does_not_leak_to_other_requests(app):
    app.config["MAX_CONTENT_LENGTH"] = None
    with app.test_request_context(BASE + "/import"):
        app.preprocess_request()
        assert request.max_content_length is not None
    with app.test_request_context("/health"):
        app.preprocess_request()
        assert request.max_content_length is None


def test_images_survive_repeated_preview_and_save_and_retry(client, app, farm_id):
    doc = document()
    response = initial_preview(client, doc, farm_id, files=[upload()])
    images = hidden_values(response, "attachment_tokens")
    assert len(images) == 1
    fetched = get_image(client, staged_url(images[0]))
    assert fetched.status_code == 200 and fetched.data == PNG
    assert fetched.mimetype == "image/png"
    with app.app_context():
        assert AnimalGroupType.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0

    response = client.post(BASE + "/import/preview", data=form(doc, farm_id, images=images))
    assert response.status_code == 200
    assert hidden_values(response, "attachment_tokens") == images
    data = form(doc, farm_id, token=preview_token(response), images=images)
    saved = client.post(BASE + "/import/confirm", data=data)
    assert saved.status_code == 302
    detail = client.get(saved.headers["Location"])
    assert "weights.png" in detail.get_data(as_text=True)
    permanent_urls = re.findall(
        r'(?:src|href)="([^"?]*/images/[^"?]+)"', detail.get_data(as_text=True)
    )
    assert permanent_urls
    permanent = get_image(client, html.unescape(permanent_urls[0]))
    assert permanent.status_code == 200 and permanent.data == PNG
    retry = client.post(BASE + "/import/confirm", data=data)
    assert retry.status_code == 302
    assert retry.headers["Location"] == saved.headers["Location"]
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 1


def test_preview_can_add_and_remove_images_without_losing_trade(client, app, farm_id):
    doc = document()
    first = initial_preview(client, doc, farm_id, files=[upload("first.png")])
    first_images = hidden_values(first, "attachment_tokens")
    data = form(doc, farm_id, images=first_images)
    data["trade_images"] = [upload("second.png")]
    added = client.post(BASE + "/import/preview", data=data)
    assert added.status_code == 200
    images = hidden_values(added, "attachment_tokens")
    assert len(images) == 2
    data = form(doc, farm_id, images=images)
    data["remove_attachment_tokens"] = [first_images[0]]
    removed = client.post(BASE + "/import/preview", data=data)
    assert removed.status_code == 200
    retained = hidden_values(removed, "attachment_tokens")
    assert len(retained) == 1 and first_images[0] not in retained
    saved = client.post(BASE + "/import/confirm", data=form(
        doc, farm_id, images=retained, token=preview_token(removed),
    ))
    assert saved.status_code == 302
    detail = client.get(saved.headers["Location"]).get_data(as_text=True)
    assert "second.png" in detail and "first.png" not in detail
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 1


def test_changing_images_requires_fresh_review(client, app, farm_id):
    doc = document()
    first = initial_preview(client, doc, farm_id, files=[upload("first.png")])
    images = hidden_values(first, "attachment_tokens")
    data = form(doc, farm_id, images=images, token=preview_token(first))
    data["trade_images"] = [upload("second.png")]
    rejected = client.post(BASE + "/import/confirm", data=data)
    assert rejected.status_code == 400
    assert len(hidden_values(rejected, "attachment_tokens")) == 2
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0
    fresh_data = form(doc, farm_id,
        images=hidden_values(rejected, "attachment_tokens"), token=preview_token(rejected))
    assert client.post(BASE + "/import/confirm", data=fresh_data).status_code == 302


def test_invalid_json_preserves_uploaded_images_for_correction(client, farm_id):
    response = client.post(BASE + "/import/preview", data={
        "trade_json": "{", "trade_images": [upload()],
    })
    assert response.status_code == 400
    images = hidden_values(response, "attachment_tokens")
    assert len(images) == 1
    response = client.post(BASE + "/import/preview", data={
        "trade_json": json.dumps(document()), "farm_ids": [farm_id], "attachment_tokens": images,
    })
    assert response.status_code == 200
    assert hidden_values(response, "attachment_tokens") == images


@pytest.mark.parametrize("content,mimetype", [
    (b"", "image/png"),
    (b"<script>alert('not an image')</script>", "image/png"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/svg+xml"),
])
def test_invalid_images_do_not_create_trade_or_stage_partial_batch(
    client, app, farm_id, isolated_image_storage, content, mimetype,
):
    response = client.post(BASE + "/import/preview", data={
        "trade_json": json.dumps(document()), "farm_ids": [farm_id],
        "trade_images": [upload("good.png"), upload("bad.png", content, mimetype)],
    })
    assert response.status_code == 400
    assert image_files(isolated_image_storage) == []
    with app.app_context():
        assert AnimalGroupType.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_staged_images_are_session_bound_and_signed(client, app, farm_id):
    doc = document()
    response = initial_preview(client, doc, farm_id, files=[upload()])
    token = hidden_values(response, "attachment_tokens")[0]
    other = app.test_client()
    assert get_image(other, staged_url(token)).status_code in (400, 403, 404)
    other.post(BASE + "/import/discard", data={"attachment_tokens": [token]})
    assert get_image(client, staged_url(token)).data == PNG
    rejected = other.post(BASE + "/import/confirm", data=form(
        doc, farm_id, images=[token], token=preview_token(response),
    ))
    assert rejected.status_code == 400
    payload, signature = token.rsplit(".", 1)
    # Change significant signature bits; the last base64 character can contain
    # unused padding bits and therefore still decode to the original signature.
    forged = payload + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    assert get_image(client, staged_url(forged)).status_code in (400, 403, 404)
    assert get_image(client, staged_url("../../../../private.png")).status_code in (400, 403, 404)
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_image_upload_count_and_byte_limits(client, farm_id, isolated_image_storage):
    response = client.post(BASE + "/import/preview", data={
        "trade_json": json.dumps(document()), "farm_ids": [farm_id],
        "trade_images": [upload(f"photo-{index}.png") for index in range(
            LivestockTradeAttachmentService.MAX_ATTACHMENTS + 1)],
    })
    assert response.status_code == 400
    assert image_files(isolated_image_storage) == []
    oversized = PNG + b"\0" * (LivestockTradeAttachmentService.MAX_ATTACHMENT_BYTES + 1 - len(PNG))
    response = client.post(BASE + "/import/preview", data={
        "trade_json": json.dumps(document()), "farm_ids": [farm_id],
        "trade_images": [upload(content=oversized)],
    })
    assert response.status_code == 400
    assert image_files(isolated_image_storage) == []


def test_tampered_staged_file_is_rejected_before_trade_is_saved(client, app, farm_id):
    doc = document()
    preview = initial_preview(client, doc, farm_id, files=[upload()])
    images = hidden_values(preview, "attachment_tokens")
    with client.session_transaction() as session:
        owner = session["livestock_trade_upload_owner"]
    with app.app_context():
        descriptor = LivestockTradeAttachmentService.describe_token(images[0], owner)
        LivestockTradeAttachmentService.staged_path(descriptor).write_bytes(PNG + b"tampered")
    assert get_image(client, staged_url(images[0])).status_code == 404
    response = client.post(BASE + "/import/confirm", data=form(
        doc, farm_id, images=images, token=preview_token(preview),
    ))
    assert response.status_code == 400
    with app.app_context():
        assert LivestockTradeAttachment.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_expired_staging_is_rejected_and_cleaned_on_next_upload(
    client, app, farm_id, isolated_image_storage, monkeypatch,
):
    old_timestamp = int(time.time()) - LivestockTradeAttachmentService.DRAFT_TTL_SECONDS - 2
    with monkeypatch.context() as expired_clock:
        expired_clock.setattr(TimestampSigner, "get_timestamp", lambda self: old_timestamp)
        preview = initial_preview(client, document(), farm_id, files=[upload()])
    token = hidden_values(preview, "attachment_tokens")[0]
    staged_files = image_files(isolated_image_storage)
    assert len(staged_files) == 1
    assert get_image(client, staged_url(token)).status_code == 404
    os.utime(staged_files[0], (old_timestamp, old_timestamp))
    initial_preview(client, document(), farm_id, files=[upload("fresh.png")])
    assert not staged_files[0].exists()
    assert len(image_files(isolated_image_storage)) == 1
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_failed_commit_removes_copied_files_but_preserves_staged_files(
    client, app, farm_id, isolated_image_storage, monkeypatch,
):
    doc = document()
    preview = initial_preview(client, doc, farm_id, files=[upload()])
    tokens = hidden_values(preview, "attachment_tokens")
    with client.session_transaction() as session:
        owner = session["livestock_trade_upload_owner"]
    staged_files = image_files(isolated_image_storage)

    def fail():
        raise ValueError("simulated commit failure")

    with app.app_context():
        images = LivestockTradeAttachmentService.resolve_tokens(tokens, owner)
        monkeypatch.setattr(db.session, "commit", fail)
        with pytest.raises(ValueError, match="simulated commit failure"):
            LivestockTradeService.import_document(doc, [farm_id], attachments=images)
        assert LivestockTradeAttachment.query.count() == AnimalGroupType.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0
    assert image_files(isolated_image_storage) == staged_files


def test_copy_failure_removes_partial_files_and_rolls_back_trade(
    client, app, farm_id, isolated_image_storage, monkeypatch,
):
    doc = document()
    preview = initial_preview(client, doc, farm_id, files=[upload("first.png"), upload("second.png")])
    tokens = hidden_values(preview, "attachment_tokens")
    with client.session_transaction() as session:
        owner = session["livestock_trade_upload_owner"]
    staged_files = image_files(isolated_image_storage)
    real_open = Path.open
    copies = []

    def fail_second_copy(path, mode="r", *args, **kwargs):
        if mode == "xb" and LivestockTradeAttachmentService.ATTACHMENT_ROOT in path.parts:
            copies.append(path)
            if len(copies) == 2:
                raise OSError("simulated disk full")
        return real_open(path, mode, *args, **kwargs)

    with app.app_context():
        images = LivestockTradeAttachmentService.resolve_tokens(tokens, owner)
        monkeypatch.setattr(Path, "open", fail_second_copy)
        with pytest.raises(OSError, match="simulated disk full"):
            LivestockTradeService.import_document(doc, [farm_id], attachments=images)
        assert len(copies) == 2
        assert LivestockTradeAttachment.query.count() == AnimalGroupType.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0
    assert image_files(isolated_image_storage) == staged_files


def test_trade_deletion_removes_permanent_images(client, app, farm_id):
    doc = document()
    preview = initial_preview(client, doc, farm_id, files=[upload()])
    saved = client.post(BASE + "/import/confirm", data=form(
        doc, farm_id, images=hidden_values(preview, "attachment_tokens"), token=preview_token(preview),
    ))
    assert saved.status_code == 302
    with app.app_context():
        attachment = LivestockTradeAttachment.query.one()
        permanent_path = LivestockTradeAttachmentService.permanent_path(attachment)
        assert permanent_path.is_file()
    assert client.post(saved.headers["Location"] + "/delete").status_code == 302
    assert not permanent_path.exists()
    with app.app_context():
        assert LivestockTradeAttachment.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_discard_removes_staged_files_and_prevents_preview_replay(
    client, app, farm_id, isolated_image_storage,
):
    doc = document()
    preview = initial_preview(client, doc, farm_id, files=[upload()])
    images = hidden_values(preview, "attachment_tokens")
    replay = form(doc, farm_id, images=images, token=preview_token(preview))
    discard_form = form_body(preview, action=BASE + "/import/discard")
    assert 'name="trade_images"' not in discard_form
    assert 'type="file"' not in discard_form
    assert re.findall(r'name="([^"]+)"', discard_form) == ["attachment_tokens"]
    discard_tokens = [html.unescape(value) for value in re.findall(
        r'name="attachment_tokens" value="([^"]+)"', discard_form,
    )]
    assert discard_tokens == images
    assert image_files(isolated_image_storage)
    discarded = client.post(BASE + "/import/discard", data={"attachment_tokens": discard_tokens})
    assert discarded.status_code == 302
    assert discarded.headers["Location"].endswith(BASE + "/import")
    assert image_files(isolated_image_storage) == []
    assert get_image(client, staged_url(images[0])).status_code in (400, 403, 404)
    assert client.post(BASE + "/import/confirm", data=replay).status_code == 400
    with app.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_failed_confirmation_retains_staged_images_and_rolls_back_trade(
    client, app, farm_id, isolated_image_storage, monkeypatch,
):
    doc = document()
    response = initial_preview(client, doc, farm_id, files=[upload()])
    images = hidden_values(response, "attachment_tokens")
    staged_files = image_files(isolated_image_storage)

    def fail(*args, **kwargs):
        raise ValueError("simulated posting failure")

    monkeypatch.setattr(LivestockTradeService, "sync_cash_transaction", fail)
    response = client.post(BASE + "/import/confirm", data=form(
        doc, farm_id, images=images, token=preview_token(response),
    ))
    assert response.status_code == 400
    assert hidden_values(response, "attachment_tokens") == images
    assert image_files(isolated_image_storage) == staged_files
    assert get_image(client, staged_url(images[0])).data == PNG
    with app.app_context():
        assert AnimalGroupType.query.count() == 0
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 0


def test_concurrent_import_with_images_cleans_losing_transaction(
    isolated_image_storage, monkeypatch,
):
    class ConcurrentConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (isolated_image_storage / "concurrent.sqlite").as_posix()

    application = create_app(ConcurrentConfig)
    application.instance_path = str(isolated_image_storage)
    with application.app_context():
        db.create_all()
        farm = Farm(name="Concurrent image import farm", active=True)
        # An existing type lets both requests reach image copying before either
        # acquires SQLite's write lock to commit the new trade and cashflow.
        animal_type = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="young")
        db.session.add_all([farm, animal_type])
        db.session.commit()
        farm_id = farm.id
        images = LivestockTradeAttachmentService.stage_uploads(
            [FileStorage(stream=io.BytesIO(PNG), filename="weights.png", content_type="image/png")],
            owner="concurrent-test-session",
        )
        staged_path = LivestockTradeAttachmentService.staged_path(images[0])
    doc = document()
    copied = Barrier(2)
    copied_paths = []
    copy_to_trade = LivestockTradeAttachmentService.copy_to_trade

    def copy_then_synchronize(cls, trade, descriptors):
        attachments = copy_to_trade(trade, descriptors)
        copied_paths.extend(cls.permanent_path(attachment) for attachment in attachments)
        copied.wait(timeout=10)
        return attachments

    monkeypatch.setattr(LivestockTradeAttachmentService, "copy_to_trade", classmethod(copy_then_synchronize))

    def run_import():
        with application.app_context():
            trade, created = LivestockTradeService.import_document(
                copy.deepcopy(doc), [farm_id], attachments=images,
            )
            return trade.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_import(), range(2)))
    assert results[0][0] == results[1][0]
    assert sorted(created for _, created in results) == [False, True]
    assert len(set(copied_paths)) == 2
    assert sum(path.is_file() for path in copied_paths) == 1
    assert staged_path.read_bytes() == PNG
    with application.app_context():
        assert LivestockTrade.query.count() == CashTransaction.query.count() == 1
        attachment = LivestockTradeAttachment.query.one()
        permanent_path = LivestockTradeAttachmentService.permanent_path(attachment)
        assert permanent_path.read_bytes() == PNG
        stored_files = [path for path in permanent_path.parents[1].rglob("*") if path.is_file()]
        assert stored_files == [permanent_path]
        assert AnimalGroupType.query.count() == 1
        db.session.remove()
        db.engine.dispose()
