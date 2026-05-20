from werkzeug.datastructures import MultiDict

from app.modules.water.forms import (
    normalize_water_asset_type_filters,
    water_asset_form_payload,
    water_asset_mass_update_form_payload,
)


def test_water_asset_form_payload_preserves_form_values():
    form = MultiDict(
        [
            ("name", "North Tank"),
            ("asset_type", "tank"),
            ("active", "1"),
            ("served_paddock_ids", "paddock-a"),
            ("served_paddock_ids", "paddock-b"),
        ]
    )

    payload = water_asset_form_payload(form, farm_id="farm-1")

    assert payload["farm_id"] == "farm-1"
    assert payload["name"] == "North Tank"
    assert payload["asset_type"] == "tank"
    assert payload["active"] == "1"
    assert payload["served_paddock_ids"] == ["paddock-a", "paddock-b"]


def test_water_mass_update_payload_uses_asset_specific_fields():
    form = MultiDict(
        [
            ("name__asset-1", "First"),
            ("name__asset-1", "Latest"),
            ("active__asset-1", "1"),
            ("served_paddock_ids__asset-1", "north"),
        ]
    )

    payload = water_asset_mass_update_form_payload(form, farm_id="farm-1", asset_id="asset-1")

    assert payload["farm_id"] == "farm-1"
    assert payload["name"] == "Latest"
    assert payload["active"] == "1"
    assert payload["served_paddock_ids"] == ["north"]


def test_normalize_water_asset_type_filters_defaults_to_all_when_filters_are_not_applied():
    selected = normalize_water_asset_type_filters(["tank", "invalid", "tank"], filters_applied=True)
    defaulted = normalize_water_asset_type_filters([], filters_applied=False)

    assert selected == ["tank"]
    assert "tank" in defaulted
    assert "trough" in defaulted
