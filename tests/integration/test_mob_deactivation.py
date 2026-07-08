from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    Paddock,
)


def _build_grazing_allocation_fixture():
    farm = Farm(name="Archive Farm", timezone="SAST")
    db.session.add(farm)
    db.session.flush()

    paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=8)
    db.session.add(paddock)
    db.session.flush()

    mob = Mob(farm_id=farm.id, name="Archive Mob", status="active")
    db.session.add(mob)
    db.session.flush()

    start_at = datetime.now(timezone.utc) - timedelta(days=2)
    session = GrazingSession(
        farm_id=farm.id,
        mob_id=mob.id,
        start_at=start_at,
        end_at=None,
    )
    db.session.add(session)
    db.session.flush()

    allocation = GrazingAllocation(
        grazing_session_id=session.id,
        paddock_id=paddock.id,
        allocation_fraction=1,
    )
    db.session.add(allocation)
    db.session.flush()

    history = GrazingAllocationLsuHistory(
        farm_id=farm.id,
        mob_id=mob.id,
        paddock_id=paddock.id,
        grazing_session_id=session.id,
        grazing_allocation_id=allocation.id,
        effective_from=start_at,
        effective_to=None,
        allocation_fraction=1,
        mob_total_lsu=0,
        allocated_lsu=0,
        source="live",
    )
    db.session.add(history)
    db.session.commit()

    return str(farm.id), str(mob.id), str(session.id), str(history.id)


def test_api_mob_deactivation_closes_open_grazing(client, app):
    with app.app_context():
        farm_id, mob_id, session_id, history_id = _build_grazing_allocation_fixture()

    response = client.post(f"/api/mobs/{mob_id}/deactivate")
    assert response.status_code == 200
    assert response.get_json()["status"] == "archived"

    with app.app_context():
        mob = Mob.query.get(mob_id)
        session = GrazingSession.query.get(session_id)
        history = GrazingAllocationLsuHistory.query.get(history_id)
        assert mob is not None
        assert mob.status == "archived"
        assert session is not None
        assert session.end_at is not None
        assert history is not None
        assert history.effective_to is not None
        assert session.end_at >= session.start_at
        assert history.effective_to >= history.effective_from
        assert str(mob.farm_id) == farm_id


def test_api_mob_patch_archive_closes_open_grazing(client, app):
    with app.app_context():
        _, mob_id, session_id, history_id = _build_grazing_allocation_fixture()

    response = client.patch(f"/api/mobs/{mob_id}", json={"status": "archived"})
    assert response.status_code == 200

    with app.app_context():
        mob = Mob.query.get(mob_id)
        session = GrazingSession.query.get(session_id)
        history = GrazingAllocationLsuHistory.query.get(history_id)
        assert mob is not None
        assert mob.status == "archived"
        assert session is not None
        assert session.end_at is not None
        assert history is not None
        assert history.effective_to is not None


def test_web_mob_deactivation_closes_open_grazing(client, app):
    with app.app_context():
        farm_id, mob_id, session_id, history_id = _build_grazing_allocation_fixture()

    response = client.post(f"/mobs/{mob_id}/deactivate")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/farms/{farm_id}")

    with app.app_context():
        mob = Mob.query.get(mob_id)
        session = GrazingSession.query.get(session_id)
        history = GrazingAllocationLsuHistory.query.get(history_id)
        assert mob is not None
        assert mob.status == "archived"
        assert session is not None
        assert session.end_at is not None
        assert history is not None
        assert history.effective_to is not None


def test_archived_mob_with_stale_open_session_is_not_treated_as_currently_grazing(client, app):
    with app.app_context():
        now = datetime.now(timezone.utc)
        farm = Farm(name="Legacy Archive Farm", timezone="SAST")
        db.session.add(farm)
        db.session.flush()

        paddock = Paddock(farm_id=farm.id, name="Legacy Camp", area_ha=12, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.flush()

        mob = Mob(farm_id=farm.id, name="Legacy Archived Mob", status="archived")
        mob.updated_at = now - timedelta(days=3)
        db.session.add(mob)
        db.session.flush()

        group = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
        db.session.add(group)
        db.session.flush()
        db.session.add(
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=group.id,
                head_count=8,
            )
        )

        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=now - timedelta(days=10),
            end_at=None,
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(
            GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=paddock.id,
                allocation_fraction=1,
            )
        )
        db.session.commit()
        paddock_id = str(paddock.id)

    response = client.get(f"/api/paddocks/{paddock_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current_lsu"] == 0.0
    assert payload["current_activity_state"] == "rested"
    assert payload["days_rested_continuously"] is not None
    assert payload["days_rested_continuously"] > 2.5
    assert payload["days_rested_continuously"] < 3.5
