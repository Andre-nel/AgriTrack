from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Farm, FenceEventMaterial, FenceSection, Paddock
from app.services.fence_service import FenceService


def _square(lon, lat, size=0.001):
    return [
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    ]


def _farm_with_adjacent_paddocks():
    farm = Farm(name="Fence Farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    left = Paddock(farm_id=farm.id, name="Left Camp", area_ha=10, grazeable_area_ha=10)
    right = Paddock(farm_id=farm.id, name="Right Camp", area_ha=12, grazeable_area_ha=12)
    db.session.add_all([left, right])
    db.session.flush()
    return farm, left, right


def test_sync_auto_sections_creates_internal_and_boundary_defaults(app):
    farm, left, right = _farm_with_adjacent_paddocks()

    result = FenceService.sync_auto_sections_from_candidates(
        str(farm.id),
        [
            {"paddock": left, "rings": [_square(0.0, 0.0)]},
            {"paddock": right, "rings": [list(reversed(_square(0.001, 0.0)))]},
        ],
    )
    db.session.commit()

    sections = FenceSection.query.filter_by(farm_id=farm.id).all()
    internal = next(section for section in sections if section.section_type == FenceSection.TYPE_INTERNAL)
    boundaries = [section for section in sections if section.section_type == FenceSection.TYPE_BOUNDARY]

    assert result == {"created": 3, "updated": 0, "retired": 0}
    assert len(boundaries) == 2
    assert internal.length_m > 100
    assert internal.height_profile == "low"
    assert internal.construction_type == "high_strung_wire"
    assert internal.wire_type == "high_strung"
    assert all(section.construction_type == "mesh" for section in boundaries)
    assert all(section.mesh_type == "wire_mesh" for section in boundaries)
    assert all(section.condition == "unknown" for section in sections)


def test_fence_event_updates_condition_and_records_material_lines(app):
    farm, left, right = _farm_with_adjacent_paddocks()
    FenceService.sync_auto_sections_from_candidates(
        str(farm.id),
        [
            {"paddock": left, "rings": [_square(0.0, 0.0)]},
            {"paddock": right, "rings": [list(reversed(_square(0.001, 0.0)))]},
        ],
    )
    section = FenceSection.query.filter_by(section_type=FenceSection.TYPE_INTERNAL).one()

    event = FenceService.create_event(
        section=section,
        event_type="maintenance",
        description="Replaced broken wire and packed a hole with stones.",
        raw_tags=["repair", "jackal"],
        condition_after="fair",
        material_rows=[
            {
                "action": "replaced",
                "material_type": "wire",
                "material_detail": "barbed strand",
                "quantity": "35",
                "unit": "m",
            },
            {
                "action": "packed",
                "material_type": "stone",
                "quantity": "2",
                "unit": "bag",
                "notes": "Packed below washout.",
            },
            {"action": "", "material_type": "", "quantity": ""},
        ],
    )
    db.session.commit()

    assert section.condition == "fair"
    assert event.event_type == "maintenance"
    assert FenceService.tags_from_csv(event.tags_csv) == ["repair", "jackal"]
    materials = FenceEventMaterial.query.filter_by(event_id=event.id).order_by(FenceEventMaterial.material_type).all()
    assert len(materials) == 2
    assert {material.material_type for material in materials} == {"stone", "wire"}
    assert next(material for material in materials if material.material_type == "wire").quantity == Decimal("35")


def test_material_rows_validate_partial_rows(app):
    with pytest.raises(ValueError, match="Material action is invalid"):
        FenceService.parse_material_rows([{"material_type": "wire", "quantity": "10"}])


def test_fence_section_update_normalizes_notes_and_tags(app):
    farm, left, _right = _farm_with_adjacent_paddocks()
    section = FenceService.create_manual_section(
        str(farm.id),
        {
            "name": "Tagged Boundary Fence",
            "section_type": "boundary",
            "paddock_a_id": str(left.id),
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.001, 0.0]]},
        },
    )

    FenceService.update_section(
        section,
        {
            "notes": "Pack stone at the washout.",
            "tags": "Jackal, repair; jackal\nboundary",
        },
    )
    db.session.commit()

    serialized = FenceService.serialize_section(section)
    assert section.tags_csv == "jackal,repair,boundary"
    assert serialized["tags"] == ["jackal", "repair", "boundary"]
    assert serialized["notes"] == "Pack stone at the washout."


def test_fence_geometry_validation_and_length_calculation(app):
    geometry, length_m = FenceService.normalize_geometry(
        {
            "type": "MultiLineString",
            "coordinates": [
                [[0.0, 0.0], [0.001, 0.0]],
                [[0.001, 0.0], [0.001, 0.001]],
            ],
        }
    )

    assert geometry["type"] == "MultiLineString"
    assert length_m > 200

    with pytest.raises(ValueError, match="LineString or MultiLineString"):
        FenceService.normalize_geometry({"type": "Point", "coordinates": [0, 0]})
    with pytest.raises(ValueError, match="two points"):
        FenceService.normalize_geometry({"type": "LineString", "coordinates": [[0, 0]]})
    with pytest.raises(ValueError, match="distinct points"):
        FenceService.normalize_geometry({"type": "LineString", "coordinates": [[0, 0], [0, 0]]})
    with pytest.raises(ValueError, match="too short"):
        FenceService.normalize_geometry({"type": "LineString", "coordinates": [[0, 0], [0.000001, 0]]})
    with pytest.raises(ValueError, match="outside valid"):
        FenceService.normalize_geometry({"type": "LineString", "coordinates": [[0, 0], [181, 0]]})


def test_map_geometry_edit_converts_auto_section_to_manual(app):
    farm, left, right = _farm_with_adjacent_paddocks()
    FenceService.sync_auto_sections_from_candidates(
        str(farm.id),
        [
            {"paddock": left, "rings": [_square(0.0, 0.0)]},
            {"paddock": right, "rings": [list(reversed(_square(0.001, 0.0)))]},
        ],
    )
    section = FenceSection.query.filter_by(section_type=FenceSection.TYPE_INTERNAL).one()
    original_length = Decimal(str(section.length_m))

    FenceService.update_section_from_map(
        section,
        {
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0005, 0.0]]},
            "condition": "bad",
        },
    )
    db.session.commit()

    assert section.source == FenceSection.SOURCE_MANUAL
    assert section.condition == "bad"
    assert section.length_m != original_length
    assert FenceService.serialize_section(section)["geometry"]["type"] == "LineString"


def test_kml_sync_preserves_manual_geometry_overrides(app):
    farm, left, right = _farm_with_adjacent_paddocks()
    rows = [
        {"paddock": left, "rings": [_square(0.0, 0.0)]},
        {"paddock": right, "rings": [list(reversed(_square(0.001, 0.0)))]},
    ]
    FenceService.sync_auto_sections_from_candidates(str(farm.id), rows)
    section = FenceSection.query.filter_by(section_type=FenceSection.TYPE_INTERNAL).one()
    FenceService.update_section_from_map(
        section,
        {"geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0005, 0.0]]}},
    )
    manual_length = section.length_m

    result = FenceService.sync_auto_sections_from_candidates(str(farm.id), rows)
    db.session.commit()

    assert result == {"created": 0, "updated": 2, "retired": 0}
    assert section.source == FenceSection.SOURCE_MANUAL
    assert section.length_m == manual_length


def test_create_manual_section_validates_paddock_relationships(app):
    farm, left, right = _farm_with_adjacent_paddocks()
    other_farm = Farm(name="Other Fence Farm", timezone="SAST", active=True)
    db.session.add(other_farm)
    db.session.flush()
    other_paddock = Paddock(farm_id=other_farm.id, name="Other Camp", area_ha=1, grazeable_area_ha=1)
    db.session.add(other_paddock)
    db.session.flush()

    section = FenceService.create_manual_section(
        str(farm.id),
        {
            "name": "Manual Internal Fence",
            "section_type": "internal",
            "paddock_a_id": str(left.id),
            "paddock_b_id": str(right.id),
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.001, 0.0]]},
            "condition": "fair",
            "electric_wire": True,
        },
    )

    assert section.source == FenceSection.SOURCE_MANUAL
    assert section.section_key.startswith("manual:")
    assert section.condition == "fair"
    assert section.electric_wire is True
    assert section.paddock_a_id < section.paddock_b_id

    with pytest.raises(ValueError, match="Paddock B must belong"):
        FenceService.create_manual_section(
            str(farm.id),
            {
                "name": "Cross Farm Fence",
                "section_type": "internal",
                "paddock_a_id": str(left.id),
                "paddock_b_id": str(other_paddock.id),
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.001, 0.0]]},
            },
        )
