from app.services.water_network_service import WaterNetworkService


def water_asset_form_payload(form, *, farm_id: str) -> dict:
    return {
        "farm_id": farm_id,
        "name": form.get("name"),
        "asset_type": form.get("asset_type"),
        "active": form.get("active", "0"),
        "needs_review": form.get("needs_review", "0"),
        "location_paddock_id": form.get("location_paddock_id"),
        "latitude": form.get("latitude"),
        "longitude": form.get("longitude"),
        "altitude_m": form.get("altitude_m"),
        "status": form.get("status"),
        "water_level": form.get("water_level"),
        "capacity_m3": form.get("capacity_m3"),
        "material": form.get("material"),
        "windmill_size_ft": form.get("windmill_size_ft"),
        "weir_size": form.get("weir_size"),
        "solar_brand": form.get("solar_brand"),
        "solar_kw": form.get("solar_kw"),
        "solar_head_m": form.get("solar_head_m"),
        "trough_size": form.get("trough_size"),
        "source_system": form.get("source_system"),
        "served_paddock_ids": form.getlist("served_paddock_ids"),
    }


def water_connection_form_payload(form, *, farm_id: str) -> dict:
    flow_type = form.get("flow_type")
    if not flow_type:
        flow_type = form.get("_flow_type")
    return {
        "farm_id": farm_id,
        "active": form.get("active", "0"),
        "flow_type": flow_type,
        "source_asset_id": form.get("source_asset_id"),
        "destination_asset_id": form.get("destination_asset_id"),
        "pump_asset_id": form.get("pump_asset_id"),
        "pipe_material": form.get("pipe_material"),
        "pipe_diameter_spec": form.get("pipe_diameter_spec"),
        "pipe_wall_thickness_spec": form.get("pipe_wall_thickness_spec"),
        "pipe_class_spec": form.get("pipe_class_spec"),
        "pipe_quality_spec": form.get("pipe_quality_spec"),
        "notes": form.get("notes"),
    }


def water_asset_mass_update_form_value(form, field_name: str, asset_id: str):
    values = form.getlist(f"{field_name}__{asset_id}")
    if not values:
        return None
    return values[-1]


def water_asset_mass_update_form_payload(form, *, farm_id: str, asset_id: str) -> dict:
    return {
        "farm_id": farm_id,
        "name": water_asset_mass_update_form_value(form, "name", asset_id),
        "active": water_asset_mass_update_form_value(form, "active", asset_id),
        "needs_review": water_asset_mass_update_form_value(form, "needs_review", asset_id),
        "location_paddock_id": water_asset_mass_update_form_value(
            form, "location_paddock_id", asset_id
        ),
        "latitude": water_asset_mass_update_form_value(form, "latitude", asset_id),
        "longitude": water_asset_mass_update_form_value(form, "longitude", asset_id),
        "altitude_m": water_asset_mass_update_form_value(form, "altitude_m", asset_id),
        "status": water_asset_mass_update_form_value(form, "status", asset_id),
        "water_level": water_asset_mass_update_form_value(form, "water_level", asset_id),
        "capacity_m3": water_asset_mass_update_form_value(form, "capacity_m3", asset_id),
        "material": water_asset_mass_update_form_value(form, "material", asset_id),
        "windmill_size_ft": water_asset_mass_update_form_value(form, "windmill_size_ft", asset_id),
        "weir_size": water_asset_mass_update_form_value(form, "weir_size", asset_id),
        "trough_size": water_asset_mass_update_form_value(form, "trough_size", asset_id),
        "solar_brand": water_asset_mass_update_form_value(form, "solar_brand", asset_id),
        "solar_kw": water_asset_mass_update_form_value(form, "solar_kw", asset_id),
        "solar_head_m": water_asset_mass_update_form_value(form, "solar_head_m", asset_id),
        "source_system": water_asset_mass_update_form_value(form, "source_system", asset_id),
        "served_paddock_ids": form.getlist(f"served_paddock_ids__{asset_id}"),
    }


def normalize_water_asset_type_filters(raw_values: list[str], *, filters_applied: bool) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in WaterNetworkService.ASSET_TYPES or value in seen:
            continue
        selected.append(value)
        seen.add(value)

    if filters_applied:
        return selected
    return list(WaterNetworkService.ASSET_TYPES)


def water_mass_update_field_specs(selected_asset_types: list[str]) -> list[dict]:
    selected_type_set = set(selected_asset_types)
    if not selected_type_set:
        return []

    all_types = set(WaterNetworkService.ASSET_TYPES)
    field_specs = [
        {
            "name": "name",
            "label": "Name",
            "kind": "text",
            "asset_types": list(all_types),
        },
        {
            "name": "active",
            "label": "Active",
            "kind": "checkbox",
            "asset_types": list(all_types),
        },
        {
            "name": "needs_review",
            "label": "Needs Review",
            "kind": "checkbox",
            "asset_types": list(all_types),
        },
        {
            "name": "location_paddock_id",
            "label": "Location Paddock",
            "kind": "select",
            "asset_types": list(all_types),
            "option_source": "paddocks",
        },
        {
            "name": "latitude",
            "label": "Latitude",
            "kind": "number",
            "asset_types": list(all_types),
            "step": "0.0000001",
        },
        {
            "name": "longitude",
            "label": "Longitude",
            "kind": "number",
            "asset_types": list(all_types),
            "step": "0.0000001",
        },
        {
            "name": "altitude_m",
            "label": "Altitude (m)",
            "kind": "number",
            "asset_types": list(all_types),
            "step": "0.01",
        },
        {
            "name": "status",
            "label": "Status",
            "kind": "select",
            "asset_types": list(all_types),
            "options_by_type": {
                asset_type: sorted(options)
                for asset_type, options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.items()
            },
        },
        {
            "name": "water_level",
            "label": "Water Level",
            "kind": "select",
            "asset_types": sorted(WaterNetworkService.WATER_LEVEL_TYPES),
            "options": list(WaterNetworkService.WATER_LEVEL_OPTIONS),
        },
        {
            "name": "capacity_m3",
            "label": "Capacity (m3)",
            "kind": "number",
            "asset_types": sorted(WaterNetworkService.CAPACITY_TYPES),
            "step": "0.01",
            "min": "0",
        },
        {
            "name": "material",
            "label": "Material",
            "kind": "select",
            "asset_types": sorted(WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE.keys()),
            "options_by_type": {
                asset_type: sorted(options)
                for asset_type, options in WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE.items()
            },
        },
        {
            "name": "windmill_size_ft",
            "label": "Windmill Size (ft)",
            "kind": "select",
            "asset_types": ["windmill"],
            "options": [str(option) for option in WaterNetworkService.WINDMILL_SIZE_OPTIONS],
        },
        {
            "name": "weir_size",
            "label": "Weir Size",
            "kind": "select",
            "asset_types": ["weir"],
            "options": list(WaterNetworkService.WEIR_SIZE_OPTIONS),
        },
        {
            "name": "trough_size",
            "label": "Trough Size",
            "kind": "select",
            "asset_types": ["trough"],
            "options": list(WaterNetworkService.TROUGH_SIZE_OPTIONS),
        },
        {
            "name": "solar_brand",
            "label": "Solar Brand",
            "kind": "text",
            "asset_types": ["solarpump"],
        },
        {
            "name": "source_system",
            "label": "Source System",
            "kind": "text",
            "asset_types": ["solarpump"],
        },
        {
            "name": "solar_kw",
            "label": "Solar kW",
            "kind": "number",
            "asset_types": ["solarpump"],
            "step": "0.01",
            "min": "0",
        },
        {
            "name": "solar_head_m",
            "label": "Solar Head (m)",
            "kind": "number",
            "asset_types": ["solarpump"],
            "step": "0.01",
            "min": "0",
        },
        {
            "name": "served_paddock_ids",
            "label": "Served Paddocks",
            "kind": "multiselect",
            "asset_types": sorted(
                WaterNetworkService.SERVED_PADDOCK_TYPES
                - WaterNetworkService.LOCATION_BOUND_SERVED_PADDOCK_TYPES
            ),
            "option_source": "paddocks",
        },
    ]

    return [field for field in field_specs if selected_type_set.intersection(field["asset_types"])]

