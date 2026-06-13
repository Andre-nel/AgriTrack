from app.extensions import db
from app.models import Farm, FenceEvent, FenceSection, Paddock
from app.services.fence_service import FenceService


def _farm_with_paddocks(name="Fence Map Farm"):
    farm = Farm(name=name, timezone="UTC", active=True)
    db.session.add(farm)
    db.session.flush()
    north = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
    south = Paddock(farm_id=farm.id, name="South Camp", area_ha=11, grazeable_area_ha=11)
    db.session.add_all([north, south])
    db.session.flush()
    return farm, north, south


def _line(lon=0.0, lat=0.0, size=0.001):
    return {"type": "LineString", "coordinates": [[lon, lat], [lon + size, lat]]}


def test_fence_page_renders_focused_map_workspace(client, app):
    with app.app_context():
        farm, north, _south = _farm_with_paddocks()
        section = FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "North Boundary Fence",
                "section_type": "boundary",
                "paddock_a_id": str(north.id),
                "geometry": _line(),
            },
        )
        db.session.commit()
        farm_id = str(farm.id)
        section_id = str(section.id)

    response = client.get(f"/farms/{farm_id}/fences")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert 'data-map-focus="fences"' in body
    assert 'data-fence-create-url="/api/fences"' in body
    assert 'data-fence-update-url-template="/api/fences/__fence_id__"' in body
    assert 'data-fence-delete-url-template="/api/fences/__fence_id__"' in body
    assert 'data-fence-map-panel' in body
    assert f'data-fence-section-id="{section_id}"' in body
    assert "North Boundary Fence" in body
    assert "Fence Register" in body
    assert f'href="/farms/{farm_id}/fences/statistics"' in body
    assert "Apply Filters" in body


def test_fence_statistics_page_summarizes_sections_events_and_materials(client, app):
    with app.app_context():
        farm, north, south = _farm_with_paddocks("Fence Stats Farm")
        bad_section = FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "Predator Boundary Fence",
                "section_type": "boundary",
                "paddock_a_id": str(north.id),
                "condition": "bad",
                "construction_type": "mesh",
                "tags": "jackal, repair",
                "electric_wire": True,
                "geometry": _line(),
            },
        )
        FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "Internal Wire Fence",
                "section_type": "internal",
                "paddock_a_id": str(north.id),
                "paddock_b_id": str(south.id),
                "condition": "good",
                "construction_type": "high_strung_wire",
                "geometry": _line(lat=0.002),
            },
        )
        FenceService.create_event(
            section=bad_section,
            event_type="maintenance",
            description="Replaced wire and packed stones.",
            raw_tags="repair",
            condition_after="fair",
            material_rows=[
                {
                    "action": "replaced",
                    "material_type": "wire",
                    "quantity": "25",
                    "unit": "m",
                }
            ],
        )
        db.session.commit()
        farm_id = str(farm.id)

    response = client.get(f"/farms/{farm_id}/fences/statistics")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "Fence Stats Farm Fence Statistics" in body
    assert "Active Sections" in body
    assert "Bad / Critical" in body
    assert "Predator Boundary Fence" not in body
    assert "Mesh" in body
    assert "High Strung Wire" in body
    assert "jackal" in body
    assert "Maintenance" in body
    assert "Wire" in body
    assert "25.00 m" in body


def test_fence_page_filters_register_by_tag_and_build(client, app):
    with app.app_context():
        farm, north, south = _farm_with_paddocks("Filtered Fence Farm")
        north_section = FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "North Repair Fence",
                "section_type": "boundary",
                "paddock_a_id": str(north.id),
                "condition": "bad",
                "construction_type": "mesh",
                "notes": "Hole near the washout.",
                "tags": "jackal, repair",
                "geometry": _line(),
            },
        )
        south_section = FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "South Good Fence",
                "section_type": "boundary",
                "paddock_a_id": str(south.id),
                "condition": "good",
                "construction_type": "high_strung_wire",
                "tags": "routine",
                "geometry": _line(lat=0.002),
            },
        )
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north_section.id)
        south_id = str(south_section.id)

    response = client.get(f"/farms/{farm_id}/fences?tag=jackal&construction_type=mesh&q=washout")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "1 of 2 active section(s) shown" in body
    assert f'data-fence-section-id="{north_id}"' in body
    assert "North Repair Fence" in body
    assert f'data-fence-section-id="{south_id}"' not in body
    assert "South Good Fence" not in body
    assert "Clear Filters" in body


def test_fence_api_creates_updates_and_serializes_map_features(client, app):
    with app.app_context():
        farm, north, south = _farm_with_paddocks()
        db.session.commit()
        farm_id = str(farm.id)
        north_id = str(north.id)
        south_id = str(south.id)

    create_response = client.post(
        "/api/fences",
        json={
            "farm_id": farm_id,
            "name": "Drawn Fence",
            "section_type": "boundary",
            "paddock_a_id": north_id,
            "condition": "fair",
            "notes": "Check erosion near the corner.",
            "tags": "Jackal, repair",
            "geometry": _line(),
        },
    )
    assert create_response.status_code == 201
    create_payload = create_response.get_json()
    fence_id = create_payload["fence"]["id"]
    assert create_payload["fence"]["source"] == FenceSection.SOURCE_MANUAL
    assert create_payload["fence"]["length_m"] > 100
    assert create_payload["fence"]["notes"] == "Check erosion near the corner."
    assert create_payload["fence"]["tags"] == ["jackal", "repair"]
    assert create_payload["feature"]["properties"]["feature_type"] == "fence_section"
    assert create_payload["feature"]["properties"]["tags"] == ["jackal", "repair"]

    update_response = client.patch(
        f"/api/fences/{fence_id}",
        json={
            "name": "Drawn Internal Fence",
            "section_type": "internal",
            "paddock_a_id": north_id,
            "paddock_b_id": south_id,
            "condition": "bad",
            "notes": "Replaced the corner note from the map editor.",
            "tags": ["internal", "urgent"],
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.002]]},
        },
    )
    assert update_response.status_code == 200
    update_payload = update_response.get_json()
    assert update_payload["fence"]["section_type"] == "internal"
    assert update_payload["fence"]["condition"] == "bad"
    assert update_payload["fence"]["notes"] == "Replaced the corner note from the map editor."
    assert update_payload["fence"]["tags"] == ["internal", "urgent"]
    assert update_payload["fence"]["geometry"]["coordinates"] == [[0.0, 0.0], [0.0, 0.002]]

    map_response = client.get(f"/farms/{farm_id}/map-data")
    assert map_response.status_code == 200
    fence_features = [
        feature
        for feature in map_response.get_json()["features"]
        if feature["properties"].get("fence_section_id") == fence_id
    ]
    assert len(fence_features) == 1
    assert fence_features[0]["geometry"]["coordinates"] == [[0.0, 0.0], [0.0, 0.002]]
    assert fence_features[0]["properties"]["tags"] == ["internal", "urgent"]


def test_fence_api_rejects_cross_farm_paddocks_and_archives(client, app):
    with app.app_context():
        farm, north, _south = _farm_with_paddocks("Archive Fence Farm")
        other_farm, other_paddock, _unused = _farm_with_paddocks("Other Archive Fence Farm")
        section = FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "Archive Me",
                "section_type": "boundary",
                "paddock_a_id": str(north.id),
                "geometry": _line(),
            },
        )
        event = FenceEvent(
            farm_id=farm.id,
            fence_section_id=section.id,
            event_type="inspection",
            tags_csv="inspection",
            description="Keep this history.",
        )
        db.session.add(event)
        db.session.commit()
        farm_id = str(farm.id)
        fence_id = str(section.id)
        north_id = str(north.id)
        other_paddock_id = str(other_paddock.id)
        event_id = str(event.id)

    rejected = client.patch(
        f"/api/fences/{fence_id}",
        json={
            "section_type": "internal",
            "paddock_a_id": north_id,
            "paddock_b_id": other_paddock_id,
        },
    )
    assert rejected.status_code == 400
    assert "Paddock B must belong" in rejected.get_json()["error"]

    delete_response = client.delete(f"/api/fences/{fence_id}")
    assert delete_response.status_code == 200
    assert delete_response.get_json()["fence"]["active"] is False
    assert delete_response.get_json()["feature"] is None

    with app.app_context():
        archived = db.session.get(FenceSection, fence_id)
        assert archived.active is False
        assert archived.source == FenceSection.SOURCE_MANUAL
        assert db.session.get(FenceEvent, event_id) is not None

    map_response = client.get(f"/farms/{farm_id}/map-data")
    assert map_response.status_code == 200
    assert all(
        feature["properties"].get("fence_section_id") != fence_id
        for feature in map_response.get_json()["features"]
    )
