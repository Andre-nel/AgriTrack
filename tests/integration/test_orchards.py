import json

import pytest
from sqlalchemy import text

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Farm, Orchard, OrchardEmitter, OrchardLine, OrchardPlant, OrchardRow, OrchardSpace, User
from app.modules.orchards.seed import FIXTURE, import_rietfontein
from app.services.farm_deletion_service import FarmDeletionService


@pytest.fixture
def orchard(app):
    with app.app_context():
        farm = Farm(name="Rietfontein")
        db.session.add(farm)
        db.session.flush()
        record, _ = import_rietfontein(farm)
        db.session.commit()
        return farm.id, record.id


def base(orchard):
    farm_id, orchard_id = orchard
    return f"/api/farms/{farm_id}/orchards/{orchard_id}"


def current(client, orchard):
    response = client.get(base(orchard))
    assert response.status_code == 200
    return response.get_json()


def change(client, orchard, path, payload=None, method="post", expected=200):
    data = {"revision": current(client, orchard)["revision"], **(payload or {})}
    response = getattr(client, method)(base(orchard) + path, json=data)
    assert response.status_code == expected, response.get_json()
    return response.get_json()


def test_seed_exact_layout_stock_and_idempotency(app, client, orchard):
    data = current(client, orchard)
    assert data["summary"] == {"spaces": 80, "planted": 69, "empty": 11, "planned": 0, "unassigned": 23}
    expected = json.loads(FIXTURE.read_text())
    for number, names in enumerate(expected["rows"], start=1):
        row = next(r for r in data["rows"] if r["name"] == f"Row {number}")
        assert row["x_m"] == (16 if number >= 7 else 0)
        assert row["y_m"] == (number - 1) * 3
        spaces = sorted((s for s in data["spaces"] if s["row_id"] == row["id"]), key=lambda s: s["position"])
        names_by_space = {p["space_id"]: p["name"] for p in data["plants"]}
        assert [names_by_space.get(s["id"]) for s in spaces] == names
        assert [s["x_m"] for s in spaces] == [row["x_m"] + i * 4 for i in range(len(names))]
        assert all(s["y_m"] == row["y_m"] for s in spaces)
    assert len(data["lines"]) == 8 and all(line["schematic"] for line in data["lines"])
    assert data["emitters"] == []
    assert all(p["planted_on"] is None for p in data["plants"])
    purchased = [p for p in data["plants"] if p["status"] == "unassigned"]
    assert all(p["purchased_on"] == "2026-10-21" and p["supplier"] == "The Pot & Barrel" for p in purchased)
    assert {item["name"]: sum(p["name"] == item["name"] for p in purchased) for item in expected["purchase"]["items"]} == {item["name"]: item["quantity"] for item in expected["purchase"]["items"]}
    row = data["rows"][0]
    change(client, orchard, f'/rows/{row["id"]}', {"name": "Edited row"}, method="patch")
    with app.app_context():
        existing, created = import_rietfontein(db.session.get(Farm, orchard[0]))
        db.session.commit()
        assert not created and existing.id == orchard[1]
    assert current(client, orchard)["summary"] == data["summary"]
    assert any(r["name"] == "Edited row" for r in current(client, orchard)["rows"])
    result = app.test_cli_runner().invoke(args=["import-orchard", "--farm", "Rietfontein"])
    assert result.exit_code == 0 and "preserved existing edits" in result.output


@pytest.mark.parametrize("planned", [False, True])
def test_remove_stock_preserves_other_records_and_releases_reservation(client, orchard, planned):
    before = current(client, orchard)
    plant = next(p for p in before["plants"] if p["status"] == "unassigned")
    if planned:
        empty = next(s for s in before["spaces"] if not any(p["space_id"] == s["id"] for p in before["plants"]))
        before = change(client, orchard, f'/plants/{plant["id"]}/placement', {"action": "reserve", "space_id": empty["id"]})
    after = change(client, orchard, f'/plants/{plant["id"]}', method="delete")
    assert after["plants"] == [p for p in before["plants"] if p["id"] != plant["id"]]
    for key in ("rows", "spaces", "lines", "emitters"):
        assert after[key] == before[key]
    assert after["summary"]["unassigned"] == before["summary"]["unassigned"] - (0 if planned else 1)
    assert after["summary"]["planned"] == 0
    assert after["summary"]["planted"] == before["summary"]["planted"]
    assert after["summary"]["empty"] == before["summary"]["empty"] + (1 if planned else 0)
    assert after["revision"] == before["revision"] + 1
    assert current(client, orchard) == after
    # A repeated request cannot delete another same-name plant.
    change(client, orchard, f'/plants/{plant["id"]}', {"revision": before["revision"]}, method="delete", expected=409)
    change(client, orchard, f'/plants/{plant["id"]}', method="delete", expected=400)
    assert current(client, orchard) == after
    if planned:
        available = next(p for p in after["plants"] if p["status"] == "unassigned")
        change(client, orchard, f'/plants/{available["id"]}/placement', {"action": "reserve", "space_id": empty["id"]})


def test_remove_stock_rejects_planted_and_stale_requests(client, orchard):
    before = current(client, orchard)
    planted = next(p for p in before["plants"] if p["status"] == "planted")
    result = change(client, orchard, f'/plants/{planted["id"]}', method="delete", expected=400)
    assert "Planted trees" in result["error"]
    assert current(client, orchard) == before
    available = next(p for p in before["plants"] if p["status"] == "unassigned")
    after = change(client, orchard, f'/plants/{available["id"]}', {"name": "Changed by another session"}, method="patch")
    change(client, orchard, f'/plants/{available["id"]}', {"revision": before["revision"]}, method="delete", expected=409)
    assert current(client, orchard) == after


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_label_position_is_independent_persistent_and_resettable(client, orchard, orientation):
    before = change(client, orchard, "/rows", {"name": "Label test", "orientation": orientation, "x_m": -4, "y_m": -6, "spacing_m": 3, "count": 3})
    row = next(r for r in before["rows"] if r["name"] == "Label test")
    assert row["label_x_m"] is None and row["label_y_m"] is None
    path = f'/rows/{row["id"]}'
    after = change(client, orchard, path, {"label_x_m": -11.25, "label_y_m": 7.5}, method="patch")
    moved = next(r for r in after["rows"] if r["id"] == row["id"])
    assert moved == {**row, "label_x_m": -11.25, "label_y_m": 7.5}
    for key in ("spaces", "plants", "lines", "emitters", "summary"):
        assert after[key] == before[key]
    assert current(client, orchard) == after
    change(client, orchard, path, {"revision": before["revision"], "label_x_m": 1, "label_y_m": 2}, method="patch", expected=409)
    assert current(client, orchard) == after
    after = change(client, orchard, path, {"x_m": 30, "y_m": 40, "resize": {"action": "extend", "end": "start", "count": 1}}, method="patch")
    moved = next(r for r in after["rows"] if r["id"] == row["id"])
    assert (moved["label_x_m"], moved["label_y_m"]) == (-11.25, 7.5)
    reset = change(client, orchard, path, {"label_x_m": None, "label_y_m": None}, method="patch")
    assert next(r for r in reset["rows"] if r["id"] == row["id"]) == {**moved, "label_x_m": None, "label_y_m": None}
    assert reset["spaces"] == after["spaces"]


@pytest.mark.parametrize("payload", [
    {"label_x_m": 1}, {"label_y_m": 1},
    {"label_x_m": None, "label_y_m": 1},
    {"label_x_m": 1, "label_y_m": None},
    {"label_x_m": True, "label_y_m": 1},
    {"label_x_m": "1", "label_y_m": 1},
    {"label_x_m": float("nan"), "label_y_m": 1},
    {"label_x_m": 1, "label_y_m": float("inf")},
    {"label_x_m": -1_000_001, "label_y_m": 1},
])
def test_invalid_label_coordinates_roll_back(client, orchard, payload):
    before = current(client, orchard)
    change(client, orchard, f'/rows/{before["rows"][0]["id"]}', {"name": "Must roll back", **payload}, method="patch", expected=400)
    assert current(client, orchard) == before


def test_rows_resize_preserve_identity_and_independent_irrigation(client, orchard):
    original = current(client, orchard)
    data = change(client, orchard, "/rows", {"name": "West column", "orientation": "vertical", "x_m": -4, "y_m": -6, "spacing_m": 3, "count": 3})
    row = next(r for r in data["rows"] if r["name"] == "West column")
    spaces = [s for s in data["spaces"] if s["row_id"] == row["id"]]
    assert {(s["x_m"], s["y_m"]) for s in spaces} == {(-4, -6), (-4, -3), (-4, 0)}
    ids = {s["id"] for s in spaces}
    data = change(client, orchard, f'/rows/{row["id"]}/resize', {"action": "extend", "end": "start", "count": 2})
    expanded = [s for s in data["spaces"] if s["row_id"] == row["id"]]
    assert ids <= {s["id"] for s in expanded}
    assert {s["position"] for s in expanded} == {-2, -1, 0, 1, 2}
    assert {(s["x_m"], s["y_m"]) for s in expanded if s["id"] in ids} == {(-4, -6), (-4, -3), (-4, 0)}
    change(client, orchard, f'/rows/{row["id"]}/resize', {"action": "trim", "end": "start", "count": 2})
    change(client, orchard, f'/rows/{row["id"]}/resize', {"action": "extend", "end": "end", "count": 2})
    change(client, orchard, f'/rows/{row["id"]}/resize', {"action": "trim", "end": "end", "count": 2})
    change(client, orchard, f'/rows/{row["id"]}', method="delete")
    existing = original["rows"][0]
    data = change(client, orchard, f'/rows/{existing["id"]}', {"x_m": -8, "y_m": -3}, method="patch")
    assert data["lines"] == original["lines"]
    assert data["plants"] == original["plants"]
    assert {s["id"] for s in data["spaces"]} == {s["id"] for s in original["spaces"]}
    before = current(client, orchard)
    change(client, orchard, f'/rows/{existing["id"]}', {"name": "Should roll back", "resize": {"action": "trim", "end": "start", "count": 1}}, method="patch", expected=400)
    assert current(client, orchard) == before
    change(client, orchard, f'/rows/{existing["id"]}', method="delete", expected=400)


def test_plant_lifecycle_and_protected_slots(client, orchard):
    data = current(client, orchard)
    plant = next(p for p in data["plants"] if p["status"] == "unassigned")
    empty = [s for s in data["spaces"] if not any(p["space_id"] == s["id"] for p in data["plants"])]
    path = f'/plants/{plant["id"]}/placement'
    data = change(client, orchard, path, {"action": "reserve", "space_id": empty[0]["id"]})
    assert data["summary"]["planned"] == 1 and data["summary"]["unassigned"] == 22
    change(client, orchard, f'/rows/{empty[0]["row_id"]}', method="delete", expected=400)
    other = next(p for p in data["plants"] if p["status"] == "unassigned")
    before = current(client, orchard)
    change(client, orchard, f'/plants/{other["id"]}/placement', {"action": "reserve", "space_id": empty[0]["id"]}, expected=400)
    assert current(client, orchard) == before
    change(client, orchard, path, {"action": "reserve", "space_id": empty[1]["id"]})
    change(client, orchard, path, {"action": "cancel"})
    assert current(client, orchard)["summary"]["unassigned"] == 23
    change(client, orchard, path, {"action": "plant", "planted_on": "2026-10-21"}, expected=400)
    change(client, orchard, path, {"action": "reserve", "space_id": empty[0]["id"]})
    change(client, orchard, path, {"action": "plant", "planted_on": "2026-10-20"}, expected=400)
    change(client, orchard, path, {"action": "plant", "planted_on": "2026-10-21", "name": "Apricot variety recorded"})
    data = change(client, orchard, path, {"action": "correct", "space_id": empty[1]["id"]})
    result = next(p for p in data["plants"] if p["id"] == plant["id"])
    assert result["status"] == "planted" and result["planted_on"] == "2026-10-21"
    assert result["name"] == "Apricot variety recorded" and result["space_id"] == empty[1]["id"]
    assert data["summary"]["planted"] == 70 and data["summary"]["planned"] == 0
    change(client, orchard, path, {"action": "cancel"}, expected=400)
    change(client, orchard, path, {"action": "reserve", "space_id": empty[0]["id"]}, expected=400)
    data = change(client, orchard, "/plants", {"name": "New variety", "quantity": 4, "purchased_on": "2026-10-21", "supplier": "Nursery"})
    assert data["summary"]["unassigned"] == 26


def test_irrigation_projection_editing_and_cascades(client, orchard):
    data = change(client, orchard, "/lines", {"name": "Test line", "vertices": [[-4, 0], [4, 0], [4, 8]], "schematic": True})
    line = next(line for line in data["lines"] if line["name"] == "Test line")
    data = change(client, orchard, f'/lines/{line["id"]}/emitters', {"kind": "sprinkler", "x_m": 1, "y_m": 1})
    emitter = data["emitters"][0]
    assert (emitter["x_m"], emitter["y_m"]) == (1, 0)
    data = change(client, orchard, f'/lines/{line["id"]}', {"vertices": [[-4, 2], [4, 2]], "name": "Verified route", "schematic": False}, method="patch")
    assert (data["emitters"][0]["x_m"], data["emitters"][0]["y_m"]) == (1, 2)
    data = change(client, orchard, f'/emitters/{emitter["id"]}', {"x_m": 10, "y_m": 20, "kind": "dripper"}, method="patch")
    assert (data["emitters"][0]["x_m"], data["emitters"][0]["y_m"]) == (4, 2)
    change(client, orchard, f'/emitters/{emitter["id"]}', method="delete")
    change(client, orchard, f'/lines/{line["id"]}/emitters', {"kind": "dripper", "x_m": 0, "y_m": 0})
    data = change(client, orchard, f'/lines/{line["id"]}', method="delete")
    assert data["emitters"] == [] and all(item["id"] != line["id"] for item in data["lines"])


@pytest.mark.parametrize("path,payload", [
    ("/rows", {"name": "Bad", "orientation": "diagonal", "count": 2, "x_m": 0, "y_m": 0, "spacing_m": 4}),
    ("/rows", {"name": "Bad", "orientation": "horizontal", "count": 0, "x_m": 0, "y_m": 0, "spacing_m": 4}),
    ("/rows", {"name": "Bad", "orientation": "horizontal", "count": 2, "x_m": float("nan"), "y_m": 0, "spacing_m": 4}),
    ("/rows", {"name": "Bad", "orientation": "horizontal", "count": 2, "x_m": 0, "y_m": 0, "spacing_m": 0}),
    ("/plants", {"name": "Test", "quantity": True}),
    ("/plants", {"name": "Test", "quantity": 2, "purchased_on": "not-a-date"}),
    ("/lines", {"name": "Bad", "vertices": [[0, 0]]}),
    ("/lines", {"name": "Bad", "vertices": [[0, 0], [0, 0]]}),
    ("/lines", {"name": "Bad", "vertices": [[0, 0], [1, float("inf")]]}),
])
def test_bad_input_rolls_back_including_revision(client, orchard, path, payload):
    before = current(client, orchard)
    change(client, orchard, path, payload, expected=400)
    assert current(client, orchard) == before


def test_conflicts_ownership_and_malformed_json(app, client, orchard):
    original = current(client, orchard)
    row = original["rows"][0]
    change(client, orchard, f'/rows/{row["id"]}', {"name": "First writer"}, method="patch")
    response = client.patch(base(orchard) + f'/rows/{row["id"]}', json={"revision": original["revision"], "name": "Stale writer"})
    assert response.status_code == 409
    assert next(r for r in current(client, orchard)["rows"] if r["id"] == row["id"])["name"] == "First writer"
    with app.app_context():
        farm = Farm(name="Other farm")
        db.session.add(farm); db.session.flush()
        record, _ = import_rietfontein(farm)
        db.session.commit()
        other = farm.id, record.id
    data = current(client, other)
    before = current(client, orchard)
    change(client, orchard, f'/rows/{data["rows"][0]["id"]}', {"name": "Wrong orchard"}, method="patch", expected=400)
    change(client, orchard, f'/rows/{data["rows"][0]["id"]}', {"label_x_m": 1, "label_y_m": 2}, method="patch", expected=400)
    other_plant = next(p for p in data["plants"] if p["status"] == "unassigned")
    change(client, orchard, f'/plants/{other_plant["id"]}', method="delete", expected=400)
    change(client, orchard, f'/lines/{data["lines"][0]["id"]}', method="delete", expected=400)
    plant = next(p for p in before["plants"] if p["status"] == "unassigned")
    change(client, orchard, f'/plants/{plant["id"]}/placement', {"action": "reserve", "space_id": data["spaces"][0]["id"]}, expected=400)
    assert current(client, orchard) == before
    assert client.get(f"/api/farms/{other[0]}/orchards/{orchard[1]}").status_code == 404
    assert client.post(base(orchard) + "/plants", json=[]).status_code == 400
    assert client.post(base(orchard) + "/plants", json={"revision": True}).status_code == 400
    assert client.post(base(orchard) + "/plants", data="broken", content_type="application/json").status_code == 400


def test_failure_after_flush_rolls_back_every_change(client, orchard, monkeypatch):
    from app.modules.orchards import services

    original = services.create_row
    before = current(client, orchard)

    def fail_after_insert(*args):
        original(*args)
        db.session.flush()
        raise ValueError("Injected failure after persistence")

    monkeypatch.setattr(services, "create_row", fail_after_insert)
    change(client, orchard, "/rows", {"name": "Rollback", "orientation": "horizontal", "x_m": 0, "y_m": 30, "spacing_m": 4, "count": 4}, expected=400)
    assert current(client, orchard) == before


def test_pages_authentication_and_creation(app, client, orchard):
    assert client.get("/orchards").status_code == 200
    assert client.get(f"/farms/{orchard[0]}/orchards/{orchard[1]}").status_code == 200
    created = client.post(f"/api/farms/{orchard[0]}/orchards", json={"name": "Second orchard"})
    assert created.status_code == 201 and created.get_json()["summary"]["spaces"] == 0
    app.config["WEB_AUTH_REQUIRED"] = True
    assert client.get("/orchards").status_code == 302
    assert client.get(base(orchard)).status_code == 401
    assert client.post(base(orchard) + "/plants", json={}).status_code == 401
    assert client.delete(base(orchard) + "/plants/any-id", json={}).status_code == 401
    assert client.patch(base(orchard) + "/rows/any-id", json={"label_x_m": 1, "label_y_m": 2}).status_code == 401
    with app.app_context():
        user = User(email="orchard@example.com", name="Owner", active=True)
        db.session.add(user); db.session.commit()
        user_id = user.id
    with client.session_transaction() as session:
        session["web_user_id"] = user_id
    assert client.get(base(orchard)).status_code == 200


def test_farm_deletion_removes_owned_orchard_without_orphans(app, client, orchard):
    data = current(client, orchard)
    change(client, orchard, f'/lines/{data["lines"][0]["id"]}/emitters', {"x_m": 0, "y_m": 0, "kind": "dripper"})
    with app.app_context():
        FarmDeletionService.delete_farm_records(db.session.get(Farm, orchard[0]))
        db.session.commit()
        for model in (Orchard, OrchardRow, OrchardSpace, OrchardPlant, OrchardLine, OrchardEmitter):
            assert model.query.count() == 0
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_migration_existing_data_and_schema_match(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "orchard.sqlite").as_posix()

    application = create_app(Config)
    runner = application.test_cli_runner()
    result = runner.invoke(args=["db", "upgrade", "d7e9f1a3b5c7"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        farm = Farm(name="Rietfontein")
        db.session.add(farm); db.session.commit()
        farm_id = farm.id
    result = runner.invoke(args=["db", "upgrade"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(args=["import-orchard"])
    assert result.exit_code == 0, result.output
    assert runner.invoke(args=["db", "upgrade"]).exit_code == 0
    with application.app_context():
        assert db.session.get(Farm, farm_id).name == "Rietfontein"
        assert OrchardPlant.query.count() == 92
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"include_object": lambda obj, name, kind, reflected, compare_to: name.startswith("orchard") if kind == "table" else True})
            assert compare_metadata(context, db.metadata) == []
    result = runner.invoke(args=["db", "downgrade", "d7e9f1a3b5c7"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        assert db.session.get(Farm, farm_id).name == "Rietfontein"
        db.session.remove()
        db.engine.dispose()


def test_label_migration_preserves_existing_orchard_records(tmp_path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "existing-orchard.sqlite").as_posix()

    application = create_app(Config)
    runner = application.test_cli_runner()
    result = runner.invoke(args=["db", "upgrade", "e8a1c3d5f709"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        farm = Farm(name="Existing farm")
        db.session.add(farm)
        db.session.flush()
        db.session.execute(text("INSERT INTO orchards (id, farm_id, name, revision) VALUES ('orchard', :farm, 'Existing orchard', 5)"), {"farm": farm.id})
        db.session.execute(text("INSERT INTO orchard_rows (id, orchard_id, name, orientation, x_m, y_m, spacing_m) VALUES ('row', 'orchard', 'Existing column', 'vertical', 4, 6, 3)"))
        db.session.execute(text("INSERT INTO orchard_spaces (id, row_id, position) VALUES ('space', 'row', -1)"))
        db.session.execute(text("INSERT INTO orchard_plants (id, orchard_id, name, status, space_id) VALUES ('plant', 'orchard', 'Existing tree', 'planted', 'space')"))
        db.session.commit()

    def existing_records():
        return {
            table: db.session.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings().all()
            for table in ("orchards", "orchard_spaces", "orchard_plants")
        }

    with application.app_context():
        before = existing_records()
    result = runner.invoke(args=["db", "upgrade"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        row = db.session.get(OrchardRow, "row")
        assert (row.label_x_m, row.label_y_m) == (None, None)
        assert (row.x_m, row.y_m, row.orientation) == (4, 6, "vertical")
        assert existing_records() == before
        row.label_x_m, row.label_y_m = -8, 12
        db.session.commit()
    result = runner.invoke(args=["db", "downgrade", "e8a1c3d5f709"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        assert existing_records() == before
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
    result = runner.invoke(args=["db", "upgrade"])
    assert result.exit_code == 0, result.output
    with application.app_context():
        assert existing_records() == before
        assert db.session.get(OrchardRow, "row").label_x_m is None
        db.session.remove()
        db.engine.dispose()
