from datetime import date, timedelta

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    Shearer,
    ShearingBale,
    ShearingBaleCode,
    ShearingEntry,
    ShearingSession,
    User,
    UserFarmRole,
)
from app.modules.analytics.services import build_shearing_analytics_report
from app.services.shearing_service import ShearingService


def _create_mobile_user(farm: Farm) -> None:
    user = User(email="shearing-mobile@example.com", name="Shearing Mobile", active=True)
    user.set_password("correct-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(UserFarmRole(user_id=user.id, farm_id=farm.id, role="manager"))


def _login(client) -> str:
    response = client.post(
        "/api/mobile/v1/auth/login",
        json={
            "email": "shearing-mobile@example.com",
            "password": "correct-password",
            "device_name": "Shearing Test Phone",
        },
    )
    assert response.status_code == 200
    return response.get_json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_shearing_service_calculates_adult_and_old_ram_multiplier_only(app):
    with app.app_context():
        farm = Farm(name="Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        shearer = ShearingService.create_shearer(farm_id=farm.id, name="Johan")
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="October sheep",
            species="Sheep",
            start_date="2026-10-01",
            end_date="2026-10-03",
            lootjie_rate="10.00",
        )
        adult_ram = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="adult")
        old_ram = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="old")
        young_ram = AnimalGroupType(species="Sheep", breed="Merino", sex="ram", age_class="young")
        ewe = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([adult_ram, old_ram, young_ram, ewe])
        db.session.flush()

        for group, quantity in [(adult_ram, 2), (old_ram, 1), (young_ram, 3), (ewe, 4)]:
            ShearingService.record_entry(
                session=session,
                work_date="2026-10-01",
                shearer_id=shearer.id,
                animal_group_type=group,
                quantity=quantity,
            )

        payload = ShearingService.serialize_session(session)
        multiplier_by_age_sex = {
            (entry["animal_group_type"]["age_class"], entry["animal_group_type"]["sex"]): entry["multiplier"]
            for entry in payload["entries"]
        }

        assert payload["totals"]["quantity"] == 10
        assert payload["totals"]["amount"] == 130.0
        assert multiplier_by_age_sex == {
            ("adult", "ewe"): 1.0,
            ("adult", "ram"): 2.0,
            ("old", "ram"): 2.0,
            ("young", "ram"): 1.0,
        }


def test_shearing_service_validates_species_dates_and_zero_delete(app):
    with app.app_context():
        farm = Farm(name="Validation Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        shearer = ShearingService.create_shearer(farm_id=farm.id, name="Piet")
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="July goats",
            species="Goat",
            start_date="2026-07-01",
            end_date="2026-07-05",
            lootjie_rate="8.50",
        )
        goat = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([goat, sheep])
        db.session.flush()

        entry = ShearingService.record_entry(
            session=session,
            work_date="2026-07-02",
            shearer_id=shearer.id,
            animal_group_type=goat,
            quantity=5,
        )
        assert entry is not None
        ShearingService.close_session(session)
        edited_entry = ShearingService.record_entry(
            session=session,
            entry_id=str(entry.id),
            work_date="2026-07-03",
            shearer_id=shearer.id,
            animal_group_type=goat,
            quantity=6,
            note="Corrected after close",
        )
        assert edited_entry is not None
        assert edited_entry.quantity == 6
        assert edited_entry.note == "Corrected after close"
        ShearingService.reopen_session(session)
        ShearingService.record_entry(
            session=session,
            work_date="2026-07-02",
            shearer_id=shearer.id,
            animal_group_type=goat,
            quantity=0,
            entry_id=str(entry.id),
        )
        assert ShearingEntry.query.count() == 0

        try:
            ShearingService.record_entry(
                session=session,
                work_date="2026-07-02",
                shearer_id=shearer.id,
                animal_group_type=sheep,
                quantity=1,
            )
        except ValueError as exc:
            assert "species" in str(exc)
        else:
            raise AssertionError("Expected species validation to fail")

        try:
            ShearingService.record_entry(
                session=session,
                work_date="2026-07-06",
                shearer_id=shearer.id,
                animal_group_type=goat,
                quantity=1,
            )
        except ValueError as exc:
            assert "after the session end date" in str(exc)
        else:
            raise AssertionError("Expected date validation to fail")


def test_shearing_service_bales_calculate_money_totals_and_validate_species(app):
    with app.app_context():
        farm = Farm(name="Bale Money Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="October wool",
            species="Sheep",
            start_date="2026-10-01",
            lootjie_rate="10.00",
        )
        wool_code = ShearingService.upsert_bale_code(
            species="Sheep",
            code="FH",
            line_type="Fleece",
            age_group="Adult",
            fineness_grade="Fine",
            length_code="b",
            fineness_micron="21.5",
            clean_yield_percent="80",
            style_character="Good character",
            consistency="Even",
            fault="None",
        )
        mohair_code = ShearingService.upsert_bale_code(species="Goat", code="KID", line_type="Fleece")

        first = ShearingService.record_bale(
            session=session,
            bale_code=wool_code,
            bale_number="1",
            weight_kg="80",
            price_per_kg="20",
        )
        second = ShearingService.record_bale(
            session=session,
            bale_code=wool_code,
            bale_number="2",
            weight_kg="35",
            total_price="700",
        )
        ShearingService.record_bale(
            session=session,
            bale_code=wool_code,
            bale_number="3",
            weight_kg="10",
        )
        ShearingService.close_session(session)
        ShearingService.record_bale(
            session=session,
            code_text="ADHOC",
            bale_number="4",
            weight_kg="5",
            total_price="50",
        )

        payload = ShearingService.serialize_session(session)
        wool_payload = ShearingService.serialize_bale_code(wool_code)
        money = payload["bale_money_totals"]
        assert wool_code.length_code == "B"
        assert wool_payload["clean_yield_percent"] == 80.0
        assert wool_payload["style_character"] == "Good character"
        assert wool_payload["consistency"] == "Even"
        assert first.total_price == 1600
        assert second.price_per_kg == 20
        assert money["total_bales"] == 4
        assert money["total_kg"] == 130.0
        assert money["priced_bales"] == 3
        assert money["priced_kg"] == 120.0
        assert money["unpriced_bales"] == 1
        assert money["total_price"] == 2350.0
        assert money["average_price_per_kg"] == 19.5833
        assert payload["bale_summary_by_code"][0]["code"] == "ADHOC"
        assert payload["bale_summary_by_code"][1]["code"] == "FH"
        assert payload["bale_summary_by_code"][1]["unpriced_bales"] == 1

        try:
            ShearingService.record_bale(
                session=session,
                bale_code=mohair_code,
                weight_kg="10",
                price_per_kg="20",
            )
        except ValueError as exc:
            assert "species" in str(exc)
        else:
            raise AssertionError("Expected bale code species validation to fail")

        try:
            ShearingService.upsert_bale_code(
                species="Goat",
                code="BADYIELD",
                clean_yield_percent="120",
            )
        except ValueError as exc:
            assert "Clean yield" in str(exc)
        else:
            raise AssertionError("Expected clean yield validation to fail")

        try:
            ShearingService.record_bale(
                session=session,
                code_text="BAD",
                weight_kg="10",
                price_per_kg="10",
                total_price="90",
            )
        except ValueError as exc:
            assert "Total price" in str(exc)
        else:
            raise AssertionError("Expected inconsistent bale price validation to fail")

        ShearingService.delete_bale(session=session, bale_id=str(first.id))
        assert ShearingBale.query.filter_by(id=first.id).first() is None


def test_shearing_analytics_report_summarizes_bales_codes_and_costs(app):
    with app.app_context():
        first_farm = Farm(name="Analytics Shearing Farm A", timezone="SAST", active=True)
        second_farm = Farm(name="Analytics Shearing Farm B", timezone="SAST", active=True)
        db.session.add_all([first_farm, second_farm])
        db.session.flush()
        shearer = ShearingService.create_shearer(farm_id=first_farm.id, name="Analytics Shearer")
        ewe = AnimalGroupType(species="Sheep", breed="Analytics Merino", sex="ewe", age_class="adult")
        ram = AnimalGroupType(species="Sheep", breed="Analytics Merino", sex="ram", age_class="adult")
        db.session.add_all([ewe, ram])
        db.session.flush()
        fh_code = ShearingService.upsert_bale_code(species="Sheep", code="FH")
        b_code = ShearingService.upsert_bale_code(species="Sheep", code="B")
        first_session = ShearingService.create_session(
            farm_id=first_farm.id,
            name="Analytics January Wool",
            species="Sheep",
            start_date="2026-01-10",
            lootjie_rate="10.00",
        )
        second_session = ShearingService.create_session(
            farm_id=second_farm.id,
            name="Analytics February Wool",
            species="Sheep",
            start_date="2026-02-10",
            lootjie_rate="8.00",
        )
        ShearingService.record_entry(
            session=first_session,
            work_date="2026-01-10",
            shearer_id=shearer.id,
            animal_group_type=ewe,
            quantity=5,
        )
        ShearingService.record_entry(
            session=first_session,
            work_date="2026-01-10",
            shearer_id=shearer.id,
            animal_group_type=ram,
            quantity=2,
        )
        ShearingService.record_entry(
            session=second_session,
            work_date="2026-02-10",
            shearer_id=shearer.id,
            animal_group_type=ewe,
            quantity=4,
        )
        ShearingService.record_bale(
            session=first_session,
            bale_code=fh_code,
            bale_number="A1",
            weight_kg="100",
            price_per_kg="20",
        )
        ShearingService.record_bale(
            session=first_session,
            bale_code=fh_code,
            bale_number="A2",
            weight_kg="50",
        )
        ShearingService.record_bale(
            session=first_session,
            bale_code=b_code,
            bale_number="A3",
            weight_kg="25",
            total_price="250",
        )
        ShearingService.record_bale(
            session=second_session,
            bale_code=fh_code,
            bale_number="B1",
            weight_kg="50",
            total_price="1250",
        )

        report = build_shearing_analytics_report(
            farm_ids=[],
            species="Sheep",
            start_date=first_session.start_date,
            end_date=second_session.start_date,
            bale_code_ids=[],
            group_by_farm=False,
        )
        summary = report["summary"]
        first_row = next(
            row for row in report["session_rows"] if row["session_name"] == "Analytics January Wool"
        )
        first_fh_row = next(
            row
            for row in report["code_rows"]
            if row["session_name"] == "Analytics January Wool" and row["code"] == "FH"
        )
        price_panel = next(
            panel
            for panel in report["chart_payload"]["panels"]
            if panel["id"] == "code-price"
        )
        fh_price_series = next(row for row in price_panel["datasets"] if row["label"] == "FH")

        assert summary["total_kg"] == 225.0
        assert summary["priced_kg"] == 175.0
        assert summary["total_money_in"] == 3500.0
        assert summary["average_price_per_kg"] == 20.0
        assert summary["shearing_cost"] == 122.0
        assert first_row["animals_shorn"] == 7
        assert first_row["average_kg_per_animal"] == 25.0
        assert first_row["average_money_per_animal"] == 321.43
        assert first_row["shearing_cost"] == 90.0
        assert first_row["cost_per_animal"] == 12.86
        assert first_fh_row["kg"] == 150.0
        assert first_fh_row["priced_kg"] == 100.0
        assert first_fh_row["average_price_per_kg"] == 20.0
        assert fh_price_series["values"] == [20.0, 25.0]

        filtered = build_shearing_analytics_report(
            farm_ids=[],
            species="Sheep",
            start_date=first_session.start_date,
            end_date=second_session.start_date,
            bale_code_ids=[str(fh_code.id)],
            group_by_farm=False,
        )
        assert filtered["summary"]["total_kg"] == 200.0
        assert filtered["summary"]["total_money_in"] == 3250.0
        assert {row["code"] for row in filtered["code_rows"]} == {"FH"}

        grouped = build_shearing_analytics_report(
            farm_ids=[],
            species="Sheep",
            start_date=first_session.start_date,
            end_date=second_session.start_date,
            bale_code_ids=[],
            group_by_farm=True,
        )
        grouped_weight_panel = next(
            panel
            for panel in grouped["chart_payload"]["panels"]
            if panel["id"] == "code-weight"
        )
        grouped_labels = {row["label"] for row in grouped_weight_panel["datasets"]}
        assert "Analytics Shearing Farm A | FH" in grouped_labels
        assert "Analytics Shearing Farm B | FH" in grouped_labels


def test_shearing_analytics_report_blanks_animal_averages_without_counts(app):
    with app.app_context():
        farm = Farm(name="Analytics No Count Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="No Count Wool",
            species="Sheep",
            start_date="2026-03-01",
            lootjie_rate="10.00",
        )
        code = ShearingService.upsert_bale_code(species="Sheep", code="NC")
        ShearingService.record_bale(
            session=session,
            bale_code=code,
            weight_kg="40",
            total_price="800",
        )

        report = build_shearing_analytics_report(
            farm_ids=[str(farm.id)],
            species="Sheep",
            start_date=session.start_date,
            end_date=session.start_date,
            bale_code_ids=[],
            group_by_farm=False,
        )
        row = report["session_rows"][0]

        assert row["animals_shorn"] == 0
        assert row["average_kg_per_animal"] is None
        assert row["average_money_per_animal"] is None
        assert row["cost_per_animal"] is None


def test_shearing_web_routes_create_session_shearer_and_entry(client, app):
    with app.app_context():
        farm = Farm(name="Web Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        farm_id = str(farm.id)
        db.session.commit()

    response = client.post(
        "/shearing/sessions",
        data={
            "farm_id": farm_id,
            "name": "Web goat shearing",
            "species": "Goat",
            "start_date": "2026-07-01",
            "end_date": "2026-07-03",
            "lootjie_rate": "9.00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        session = ShearingSession.query.filter_by(farm_id=farm_id).one()
        session_id = str(session.id)

    client.post(
        "/shearing/shearers",
        data={"farm_id": farm_id, "name": "Klaas", "return_session_id": session_id},
    )
    with app.app_context():
        shearer = Shearer.query.filter_by(name_key="klaas").one()

    client.post(
        f"/shearing/sessions/{session_id}/entries",
        data={
            "work_date": "2026-07-02",
            "shearer_id": str(shearer.id),
            "breed": "Boer",
            "sex": "ram",
            "age_class": "old",
            "quantity": "3",
        },
    )

    with app.app_context():
        session = ShearingSession.query.filter_by(id=session_id).one()
        payload = ShearingService.serialize_session(session)
        assert payload["totals"] == {"quantity": 3, "amount": 54.0}
        entry_id = str(session.entries[0].id)

    client.post(
        "/shearing/shearers",
        data={"farm_id": farm_id, "name": "Ben", "return_session_id": session_id},
    )
    with app.app_context():
        second_shearer = Shearer.query.filter_by(name_key="ben").one()
        ewe_group = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        db.session.add(ewe_group)
        db.session.commit()
        second_shearer_id = str(second_shearer.id)
        ewe_group_id = str(ewe_group.id)

    client.post(
        f"/shearing/sessions/{session_id}/entries",
        data={
            "entry_id": entry_id,
            "work_date": "2026-07-03",
            "shearer_id": second_shearer_id,
            "animal_group_type_id": ewe_group_id,
            "quantity": "5",
            "unit_rate": "12.00",
            "original_unit_rate": "18.00",
            "line_amount": "70.00",
            "original_line_amount": "54.00",
            "note": "Adjusted daily row",
        },
    )

    with app.app_context():
        session = ShearingSession.query.filter_by(id=session_id).one()
        assert len(session.entries) == 1
        edited_entry = session.entries[0]
        payload = ShearingService.serialize_session(session)
        assert edited_entry.work_date.isoformat() == "2026-07-03"
        assert str(edited_entry.shearer_id) == second_shearer_id
        assert str(edited_entry.animal_group_type_id) == ewe_group_id
        assert edited_entry.quantity == 5
        assert edited_entry.note == "Adjusted daily row"
        assert payload["totals"] == {"quantity": 5, "amount": 70.0}
        assert payload["entries"][0]["unit_rate"] == 12.0
        assert payload["entries"][0]["line_amount"] == 70.0

    client.post(
        "/shearing/bale-codes",
        data={
            "farm_id": farm_id,
            "species": "Goat",
            "code": "KID",
            "line_type": "Fleece",
            "age_group": "Kid",
            "fineness_grade": "Kid",
            "length_code": "B",
            "clean_yield_percent": "82",
            "style_character": "Good ringlet",
            "consistency": "Even",
        },
    )
    with app.app_context():
        bale_code = ShearingBaleCode.query.filter_by(species="Goat", code_key="kid").one()
        assert str(bale_code.clean_yield_percent) == "82.00"
        assert bale_code.style_character == "Good ringlet"

    client.post(
        f"/shearing/sessions/{session_id}/bales",
        data={
            "bale_code_id": str(bale_code.id),
            "bale_number": "B1",
            "weight_kg": "25",
            "price_per_kg": "50",
        },
    )

    with app.app_context():
        session = ShearingSession.query.filter_by(id=session_id).one()
        payload = ShearingService.serialize_session(session)
        assert payload["bale_money_totals"]["total_bales"] == 1
        assert payload["bale_money_totals"]["total_price"] == 1250.0

    page = client.get(f"/shearing/sessions/{session_id}")
    assert page.status_code == 200
    assert b"Shearer Payouts" in page.data
    assert b"Bales / Money" in page.data
    text = page.get_data(as_text=True)
    assert "<summary><strong>Edit Session</strong></summary>" in text
    assert "<summary><strong>Add Shearer</strong></summary>" in text
    assert "<summary><strong>Record Daily Count</strong></summary>" in text
    assert "<summary><strong>Bales / Money</strong></summary>" in text


def test_shearing_analytics_web_route_renders_empty_and_landing_link(client):
    page = client.get("/analytics/shearing")
    assert page.status_code == 200
    assert b"Shearing Analytics" in page.data
    assert b"No shearing sessions match the selected filters" in page.data

    landing = client.get("/analytics")
    assert landing.status_code == 200
    assert b"/analytics/shearing" in landing.data
    assert b"Open Shearing Analytics" in landing.data


def test_shearing_analytics_default_window_handles_future_sessions(client, app):
    future_start = date.today() + timedelta(days=30)
    with app.app_context():
        farm = Farm(name="Future Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="Future Wool",
            species="Sheep",
            start_date=future_start.isoformat(),
            lootjie_rate="10.00",
        )
        code = ShearingService.upsert_bale_code(species="Sheep", code="FUTURE")
        ShearingService.record_bale(
            session=session,
            bale_code=code,
            weight_kg="30",
            total_price="600",
        )
        db.session.commit()

    page = client.get("/analytics/shearing")
    assert page.status_code == 200
    assert b"Future Wool" in page.data
    assert future_start.isoformat().encode() in page.data


def test_shearing_analytics_web_route_filters_and_groups(client, app):
    with app.app_context():
        first_farm = Farm(name="Analytics Route Farm A", timezone="SAST", active=True)
        second_farm = Farm(name="Analytics Route Farm B", timezone="SAST", active=True)
        db.session.add_all([first_farm, second_farm])
        db.session.flush()
        sheep_code = ShearingService.upsert_bale_code(species="Sheep", code="ROUTE-FH")
        goat_code = ShearingService.upsert_bale_code(species="Goat", code="ROUTE-KID")
        first_session = ShearingService.create_session(
            farm_id=first_farm.id,
            name="Route Sheep A",
            species="Sheep",
            start_date="2026-04-01",
            lootjie_rate="10.00",
        )
        second_session = ShearingService.create_session(
            farm_id=second_farm.id,
            name="Route Sheep B",
            species="Sheep",
            start_date="2026-04-02",
            lootjie_rate="10.00",
        )
        goat_session = ShearingService.create_session(
            farm_id=first_farm.id,
            name="Route Goat",
            species="Goat",
            start_date="2026-04-03",
            lootjie_rate="10.00",
        )
        ShearingService.record_bale(
            session=first_session,
            bale_code=sheep_code,
            weight_kg="100",
            total_price="2000",
        )
        ShearingService.record_bale(
            session=second_session,
            bale_code=sheep_code,
            weight_kg="50",
            total_price="1000",
        )
        ShearingService.record_bale(
            session=goat_session,
            bale_code=goat_code,
            weight_kg="25",
            total_price="500",
        )
        db.session.commit()
        first_farm_id = str(first_farm.id)
        sheep_code_id = str(sheep_code.id)

    page = client.get(
        "/analytics/shearing",
        query_string={
            "farm_id": first_farm_id,
            "species": "Sheep",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "bale_code_id": sheep_code_id,
            "group_by_farm": "1",
        },
    )
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Route Sheep A" in text
    assert "Route Sheep B" not in text
    assert "Route Goat" not in text
    assert "Analytics Route Farm A | ROUTE-FH" in text
    assert "100.000" in text
    assert "R 2,000.00" in text


def test_mobile_shearing_snapshot_and_sync_commands(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_mobile_user(farm)
        farm_id = str(farm.id)
        db.session.commit()

    token = _login(client)
    commands = [
        {
            "client_command_id": "shearer-1",
            "type": "shearer.create",
            "farm_id": farm_id,
            "payload": {"id": "11111111-1111-1111-1111-111111111111", "name": "Mobile Shearer"},
        },
        {
            "client_command_id": "session-1",
            "type": "shearing_session.create",
            "farm_id": farm_id,
            "payload": {
                "id": "22222222-2222-2222-2222-222222222222",
                "name": "Mobile sheep shearing",
                "species": "Sheep",
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
                "lootjie_rate": 10,
            },
        },
        {
            "client_command_id": "entry-1",
            "type": "shearing_entry.record",
            "farm_id": farm_id,
            "payload": {
                "id": "33333333-3333-3333-3333-333333333333",
                "session_id": "22222222-2222-2222-2222-222222222222",
                "work_date": "2026-10-01",
                "shearer_id": "11111111-1111-1111-1111-111111111111",
                "animal_group_type": {
                    "species": "Sheep",
                    "breed": "Merino",
                    "sex": "ram",
                    "age_class": "adult",
                },
                "quantity": 4,
            },
        },
        {
            "client_command_id": "bale-code-1",
            "type": "shearing_bale_code.upsert",
            "farm_id": farm_id,
            "payload": {
                "id": "44444444-4444-4444-4444-444444444444",
                "species": "Sheep",
                "code": "FH",
                "line_type": "Fleece",
                "age_group": "Adult",
                "fineness_grade": "Fine",
                "length_code": "B",
                "fineness_micron": 21.5,
                "clean_yield_percent": 80,
                "style_character": "Good character",
                "consistency": "Even",
                "fault": "None",
            },
        },
        {
            "client_command_id": "bale-1",
            "type": "shearing_bale.record",
            "farm_id": farm_id,
            "payload": {
                "id": "55555555-5555-5555-5555-555555555555",
                "session_id": "22222222-2222-2222-2222-222222222222",
                "bale_code_id": "44444444-4444-4444-4444-444444444444",
                "bale_number": "B1",
                "weight_kg": 80,
                "price_per_kg": 20,
            },
        },
    ]

    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": commands},
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert [row["status"] for row in response.get_json()["results"]] == [
        "applied",
        "applied",
        "applied",
        "applied",
        "applied",
    ]

    duplicate = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": [commands[2]]},
        headers=_auth(token),
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["results"][0]["duplicate"] is True

    snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    assert snapshot.status_code == 200
    payload = snapshot.get_json()
    assert payload["shearers"][0]["name"] == "Mobile Shearer"
    assert payload["shearing_bale_codes"][0]["code"] == "FH"
    assert payload["shearing_bale_codes"][0]["length_code"] == "B"
    assert payload["shearing_bale_codes"][0]["clean_yield_percent"] == 80.0
    assert payload["shearing_bale_codes"][0]["style_character"] == "Good character"
    session = payload["shearing_sessions"][0]
    assert session["totals"] == {"quantity": 4, "amount": 80.0}
    assert session["entries"][0]["multiplier"] == 2.0
    assert session["bales"][0]["total_price"] == 1600.0
    assert session["bale_money_totals"]["total_kg"] == 80.0
    assert session["bale_summary_by_code"][0]["code"] == "FH"

    corrections = [
        {
            "client_command_id": "shearer-2",
            "type": "shearer.create",
            "farm_id": farm_id,
            "payload": {"id": "66666666-6666-6666-6666-666666666666", "name": "Correct Shearer"},
        },
        {
            "client_command_id": "entry-correction-1",
            "type": "shearing_entry.record",
            "farm_id": farm_id,
            "payload": {
                "id": "33333333-3333-3333-3333-333333333333",
                "session_id": "22222222-2222-2222-2222-222222222222",
                "work_date": "2026-10-02",
                "shearer_id": "66666666-6666-6666-6666-666666666666",
                "animal_group_type": {
                    "species": "Sheep",
                    "breed": "Dorper",
                    "sex": "ewe",
                    "age_class": "adult",
                },
                "quantity": 7,
                "note": "Corrected row",
            },
        },
    ]
    correction_response = client.post(
        "/api/mobile/v1/sync/commands",
        json={"commands": corrections},
        headers=_auth(token),
    )
    assert correction_response.status_code == 200
    assert [row["status"] for row in correction_response.get_json()["results"]] == [
        "applied",
        "applied",
    ]

    corrected_snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    corrected_session = corrected_snapshot.get_json()["shearing_sessions"][0]
    corrected_entry = corrected_session["entries"][0]
    assert corrected_session["totals"] == {"quantity": 7, "amount": 70.0}
    assert corrected_entry["work_date"] == "2026-10-02"
    assert corrected_entry["shearer_name"] == "Correct Shearer"
    assert corrected_entry["animal_group_type"]["breed"] == "Dorper"
    assert corrected_entry["quantity"] == 7
    assert corrected_entry["note"] == "Corrected row"

    delete_response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "entry-delete-1",
                    "type": "shearing_entry.delete",
                    "farm_id": farm_id,
                    "payload": {
                        "session_id": "22222222-2222-2222-2222-222222222222",
                        "entry_id": "33333333-3333-3333-3333-333333333333",
                    },
                }
            ]
        },
        headers=_auth(token),
    )
    assert delete_response.status_code == 200
    assert delete_response.get_json()["results"][0]["status"] == "applied"
    deleted_snapshot = client.get(f"/api/mobile/v1/farms/{farm_id}/snapshot", headers=_auth(token))
    deleted_session = deleted_snapshot.get_json()["shearing_sessions"][0]
    assert deleted_session["totals"] == {"quantity": 0, "amount": 0.0}
    assert deleted_session["entries"] == []


def test_mobile_shearing_rejects_invalid_species(client, app):
    with app.app_context():
        farm = Farm(name="Mobile Invalid Shearing Farm", timezone="SAST", active=True)
        db.session.add(farm)
        db.session.flush()
        _create_mobile_user(farm)
        farm_id = str(farm.id)
        shearer = ShearingService.create_shearer(farm_id=farm.id, name="Valid Shearer")
        session = ShearingService.create_session(
            farm_id=farm.id,
            name="Goat shearing",
            species="Goat",
            start_date="2026-07-01",
            lootjie_rate="8",
        )
        db.session.commit()
        shearer_id = str(shearer.id)
        session_id = str(session.id)

    token = _login(client)
    response = client.post(
        "/api/mobile/v1/sync/commands",
        json={
            "commands": [
                {
                    "client_command_id": "bad-species-entry",
                    "type": "shearing_entry.record",
                    "farm_id": farm_id,
                    "payload": {
                        "session_id": session_id,
                        "work_date": "2026-07-01",
                        "shearer_id": shearer_id,
                        "animal_group_type": {
                            "species": "Sheep",
                            "breed": "Merino",
                            "sex": "ewe",
                            "age_class": "adult",
                        },
                        "quantity": 1,
                    },
                }
            ]
        },
        headers=_auth(token),
    )

    assert response.status_code == 200
    result = response.get_json()["results"][0]
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_command"


def test_shearer_can_work_sessions_on_multiple_farms(app):
    with app.app_context():
        first_farm = Farm(name="First Shearing Farm", timezone="SAST", active=True)
        second_farm = Farm(name="Second Shearing Farm", timezone="SAST", active=True)
        db.session.add_all([first_farm, second_farm])
        db.session.flush()
        shearer = ShearingService.create_shearer(farm_id=first_farm.id, name="Cross Farm Shearer")
        same_shearer = ShearingService.create_shearer(farm_id=second_farm.id, name="Cross Farm Shearer")
        first_session = ShearingService.create_session(
            farm_id=first_farm.id,
            name="First farm sheep",
            species="Sheep",
            start_date="2026-10-01",
            lootjie_rate="10.00",
        )
        second_session = ShearingService.create_session(
            farm_id=second_farm.id,
            name="Second farm goats",
            species="Goat",
            start_date="2026-07-01",
            lootjie_rate="8.00",
        )
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        goat = AnimalGroupType(species="Goat", breed="Boer", sex="ewe", age_class="adult")
        db.session.add_all([sheep, goat])
        db.session.flush()

        ShearingService.record_entry(
            session=first_session,
            work_date="2026-10-01",
            shearer_id=shearer.id,
            animal_group_type=sheep,
            quantity=6,
        )
        ShearingService.record_entry(
            session=second_session,
            work_date="2026-07-01",
            shearer_id=same_shearer.id,
            animal_group_type=goat,
            quantity=5,
        )

        assert same_shearer.id == shearer.id
        assert ShearingService.serialize_session(first_session)["by_shearer"][0]["quantity"] == 6
        assert ShearingService.serialize_session(second_session)["by_shearer"][0]["quantity"] == 5
