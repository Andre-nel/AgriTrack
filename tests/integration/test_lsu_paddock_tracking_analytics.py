from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, Mob, Paddock
from app.services.grazing_service import GrazingService


def _create_lsu_tracking_dataset():
    farm = Farm(name="LSU Tracking Farm", timezone="SAST", default_stocking_rate_ha_per_lsu=6)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North 1", area_ha=12, grazeable_area_ha=10)
    south = Paddock(farm_id=farm.id, name="South 2", area_ha=9, grazeable_area_ha=8)
    mob = Mob(farm_id=farm.id, name="Mixed Mob", status="active")
    db.session.add_all([north, south, mob])
    db.session.flush()
    cattle = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
    sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
    db.session.add_all([cattle, sheep])
    db.session.flush()
    db.session.add_all(
        [
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=cattle.id,
                head_count=6,
            ),
            AnimalGroupBalance(
                mob_id=mob.id,
                animal_group_type_id=sheep.id,
                head_count=12,
            ),
        ]
    )
    GrazingService.open_session(
        farm_id=farm.id,
        mob_id=mob.id,
        start_at=datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc),
        allocations=[{"paddock_id": str(north.id), "allocation_fraction": "1.0"}],
    )
    db.session.commit()
    return farm, north, south


def test_lsu_paddock_tracking_page_renders_filters_chart_and_summary(client, app):
    with app.app_context():
        farm, north, _south = _create_lsu_tracking_dataset()
        farm_id = str(farm.id)
        north_id = str(north.id)

    response = client.get(
        f"/analytics/lsu-paddock-tracking?farm_id={farm_id}&paddock_id={north_id}"
        "&species=Cattle&start_date=2026-03-01&end_date=2026-03-04"
    )

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "LSU Paddock Tracking" in body
    assert "North 1" in body
    assert "Cattle" in body
    assert "Total LSU-days" in body
    assert "LSU-days/ha" in body
    assert "lsuPaddockTrackingData" in body


def test_lsu_paddock_tracking_supports_species_grouping_and_paddock_split(client, app):
    with app.app_context():
        farm, north, _south = _create_lsu_tracking_dataset()
        farm_id = str(farm.id)
        north_id = str(north.id)

    response = client.get(
        f"/analytics/lsu-paddock-tracking?farm_id={farm_id}&paddock_id={north_id}"
        "&metric=current_lsu&plot_mode=paddock&group_by_species=1"
        "&start_date=2026-03-01&end_date=2026-03-04"
    )

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Separate Plot Per Paddock" in body
    assert "Group By Species" in body
    assert "North 1 | Cattle" in body
    assert "North 1 | Sheep" in body


def test_lsu_paddock_tracking_supports_head_count_metric_and_min_filter(client, app):
    with app.app_context():
        farm, north, _south = _create_lsu_tracking_dataset()
        farm_id = str(farm.id)
        north_id = str(north.id)

    response = client.get(
        f"/analytics/lsu-paddock-tracking?farm_id={farm_id}&paddock_id={north_id}"
        "&metric=head_count&min_value=10&start_date=2026-03-01&end_date=2026-03-04"
    )

    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Head Count" in body
    assert "Minimum Selected Metric" in body
    assert "reaches at least 10" in body


def test_lsu_paddock_tracking_rejects_invalid_date_window(client, app):
    with app.app_context():
        farm, _north, _south = _create_lsu_tracking_dataset()
        farm_id = str(farm.id)

    response = client.get(
        f"/analytics/lsu-paddock-tracking?farm_id={farm_id}"
        "&start_date=2026-03-05&end_date=2026-03-01"
    )

    assert response.status_code == 302
    assert "/analytics/lsu-paddock-tracking" in response.headers["Location"]


def test_lsu_paddock_tracking_rejects_invalid_min_filter(client, app):
    with app.app_context():
        farm, _north, _south = _create_lsu_tracking_dataset()
        farm_id = str(farm.id)

    response = client.get(
        f"/analytics/lsu-paddock-tracking?farm_id={farm_id}&metric=head_count&min_value=bad"
    )

    assert response.status_code == 302
    assert "/analytics/lsu-paddock-tracking" in response.headers["Location"]
