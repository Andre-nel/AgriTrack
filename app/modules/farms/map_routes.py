from datetime import date, datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from flask import current_app, jsonify, url_for

from app.models import Farm, GrazingAllocation, GrazingSession, Mob, Paddock
from app.services.fence_service import FenceService
from app.services.gate_service import GateService
from app.services.grazing_history_service import GrazingHistoryService
from app.services.paddock_service import PaddockService
from app.services.reporting_service import ReportingService
from app.services.water_network_service import WaterNetworkService


def _normalize_name(value: str | None) -> str:
    return PaddockService.normalize_name(value).lower()


def _round_float(value: float | None, precision: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), precision)


def _parse_kml_ring(raw: str | None) -> list[list[float]]:
    coords = []
    for token in (raw or "").replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        coords.append([lon, lat])

    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _placemark_geometry(placemark, namespace: dict[str, str]) -> dict | None:
    polygons = []
    for polygon in placemark.findall(".//k:Polygon", namespace):
        ring_text = polygon.findtext(
            ".//k:outerBoundaryIs/k:LinearRing/k:coordinates",
            default="",
            namespaces=namespace,
        )
        ring = _parse_kml_ring(ring_text)
        if len(ring) >= 4:
            polygons.append([ring])

    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _load_farm_kml_features(farm_name: str) -> tuple[list[dict], str]:
    kml_path = Path(current_app.instance_path) / "maps" / f"{farm_name}.kml"
    if not kml_path.exists():
        raise FileNotFoundError(kml_path)

    namespace = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(kml_path).getroot()
    features = []
    for placemark in root.findall(".//k:Placemark", namespace):
        name = placemark.findtext("k:name", default="", namespaces=namespace).strip()
        geometry = _placemark_geometry(placemark, namespace)
        if not name or geometry is None:
            continue
        features.append(
            {
                "name": name,
                "normalized_name": _normalize_name(name),
                "geometry": geometry,
            }
        )

    return features, str(kml_path)


def _active_grazing_snapshot_by_paddock(farm_id: str) -> dict[str, dict]:
    rows = (
        GrazingAllocation.query.join(GrazingSession).join(Mob)
        .filter(
            GrazingSession.farm_id == farm_id,
            GrazingSession.end_at.is_(None),
            Mob.status == "active",
        )
        .all()
    )

    by_paddock = {}
    for allocation in rows:
        paddock_key = str(allocation.paddock_id)
        snapshot = by_paddock.setdefault(
            paddock_key,
            {"current_lsu": 0.0, "mobs": [], "species_heads": {}},
        )

        mob = allocation.grazing_session.mob
        fraction = ReportingService.allocation_effective_fraction(allocation)
        allocated_lsu = ReportingService.allocation_lsu(allocation)
        snapshot["current_lsu"] += allocated_lsu
        snapshot["mobs"].append(
            {
                "mob_id": str(mob.id),
                "mob_name": mob.name,
                "mob_url": url_for("web.mob_detail", mob_id=mob.id),
                "allocation_pct": _round_float(fraction * 100.0, 2),
                "allocated_lsu": _round_float(allocated_lsu, 3),
            }
        )

        for group_row in ReportingService.allocation_group_head_rows(allocation):
            species = group_row["animal_group_type"].species
            current_head = snapshot["species_heads"].get(species, 0.0)
            snapshot["species_heads"][species] = current_head + group_row["head_count"]

    for snapshot in by_paddock.values():
        snapshot["mobs"].sort(key=lambda item: item["allocated_lsu"], reverse=True)
        species_rows = [
            {"species": species, "head": _round_float(head, 2)}
            for species, head in sorted(snapshot["species_heads"].items(), key=lambda row: row[0])
        ]
        snapshot["species_heads"] = species_rows
        snapshot["current_lsu"] = _round_float(snapshot["current_lsu"], 3)

    return by_paddock


def _paddock_map_properties(
    paddock: Paddock,
    farm: Farm,
    active_snapshot: dict,
    *,
    water_alert: dict | None = None,
) -> dict:
    today = date.today()
    now_dt = datetime.utcnow()
    year_start_dt = datetime(today.year, 1, 1)

    grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
    area_ha = float(paddock.area_ha or 0)
    effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha

    effective_stocking_rate = float(
        paddock.stocking_rate_ha_per_lsu_override or farm.default_stocking_rate_ha_per_lsu
    )
    grazing_capacity_sdh = (365.0 / effective_stocking_rate) if effective_stocking_rate > 0 else None

    year_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
        paddock,
        period_start=year_start_dt,
        period_end=now_dt,
    )
    sdh_used_this_year = (year_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    grazing_pressure_ratio = (
        (sdh_used_this_year / grazing_capacity_sdh)
        if sdh_used_this_year is not None and grazing_capacity_sdh
        else None
    )

    active = active_snapshot.get(str(paddock.id), {})
    current_lsu = active.get("current_lsu", 0.0)
    paddock_ha_per_current_lsu = (area_ha / current_lsu) if current_lsu > 0 else None
    current_activity = ReportingService.paddock_continuous_activity(paddock)
    water_alert = water_alert or {}
    return {
        "feature_type": "paddock",
        "farm_id": str(farm.id),
        "farm_name": farm.name,
        "paddock_id": str(paddock.id),
        "paddock_url": url_for("web.paddock_detail", paddock_id=paddock.id),
        "name": paddock.name,
        "status": paddock.status,
        "area_ha": _round_float(area_ha, 2),
        "grazeable_area_ha": _round_float(grazeable_area_ha, 2),
        "effective_area_ha": _round_float(effective_area_ha, 2),
        "effective_stocking_rate_ha_per_lsu": _round_float(effective_stocking_rate, 2),
        "grazing_capacity_sdh": _round_float(grazing_capacity_sdh, 4),
        "sdh_used_this_year": _round_float(sdh_used_this_year, 4),
        "grazing_pressure_ratio": _round_float(grazing_pressure_ratio, 4),
        "current_lsu": current_lsu,
        "paddock_ha_per_current_lsu": _round_float(paddock_ha_per_current_lsu, 2),
        "current_activity_state": current_activity["current_activity_state"],
        "current_activity_label": current_activity["current_activity_label"],
        "current_activity_days": _round_float(current_activity["current_activity_days"], 2),
        "days_grazed_continuously": _round_float(
            current_activity["days_grazed_continuously"], 2
        ),
        "days_rested_continuously": _round_float(
            current_activity["days_rested_continuously"], 2
        ),
        "mobs": active.get("mobs", []),
        "species_heads": active.get("species_heads", []),
        "water_alert_level": water_alert.get("level"),
        "water_alert_message": water_alert.get("message"),
    }


def _build_farm_map_feature_collection(
    farm: Farm,
    *,
    include_water: bool = False,
    allow_missing_kml: bool = False,
) -> dict:
    expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
    if not expected_kml_path.exists() and not allow_missing_kml:
        raise FileNotFoundError(expected_kml_path)

    kml_features = []
    warnings = []
    if expected_kml_path.exists():
        kml_features, kml_path = _load_farm_kml_features(farm.name)
    else:
        kml_path = str(expected_kml_path)
        warnings.append(f"Farm map KML file not found: {expected_kml_path}")
    paddocks = list(Paddock.query.filter_by(farm_id=farm.id, status="active").all())
    paddock_by_name = {_normalize_name(p.name): p for p in paddocks}
    farm_name_key = _normalize_name(farm.name)
    active_snapshot = _active_grazing_snapshot_by_paddock(str(farm.id))
    water_network_state = (
        WaterNetworkService.network_state_for_farm(str(farm.id)) if include_water else None
    )
    paddock_water_alerts = (water_network_state or {}).get("paddock_alerts", {})

    feature_collection = []
    matched_paddock_ids = set()
    unmatched_placemarks = []

    for item in kml_features:
        normalized_name = item["normalized_name"]
        paddock = paddock_by_name.get(normalized_name)
        if paddock:
            properties = _paddock_map_properties(
                paddock,
                farm,
                active_snapshot,
                water_alert=paddock_water_alerts.get(str(paddock.id)),
            )
            matched_paddock_ids.add(str(paddock.id))
        else:
            feature_type = "farm_boundary" if normalized_name == farm_name_key else "unmatched"
            properties = {
                "feature_type": feature_type,
                "farm_id": str(farm.id),
                "farm_name": farm.name,
                "name": item["name"],
            }
            if feature_type == "unmatched":
                unmatched_placemarks.append(item["name"])

        feature_collection.append(
            {
                "type": "Feature",
                "geometry": item["geometry"],
                "properties": properties,
            }
        )

    paddocks_without_kml = [
        p.name
        for p in sorted(paddocks, key=lambda row: row.name.lower())
        if str(p.id) not in matched_paddock_ids
    ]
    if include_water:
        water_features = WaterNetworkService.active_map_features_for_farm(
            str(farm.id),
            network_state=water_network_state,
        )
        for feature in water_features:
            properties = feature.get("properties", {})
            if properties.get("feature_type") == "water_asset" and properties.get("id"):
                properties["asset_workspace_url"] = url_for(
                    "web.farm_water_workspace",
                    farm_id=farm.id,
                )
        feature_collection.extend(water_features)
        if warnings and feature_collection:
            warnings.append("Showing water network features without farm KML polygons.")

    gate_features = GateService.active_map_features_for_farm(str(farm.id))
    for feature in gate_features:
        properties = feature.get("properties", {})
        gate_id = properties.get("gate_id") or properties.get("id")
        if gate_id:
            properties["gate_detail_url"] = url_for(
                "web.farm_gate_detail",
                farm_id=farm.id,
                gate_id=gate_id,
            )
            properties["gate_state_url"] = url_for(
                "web.update_farm_gate_state_form",
                farm_id=farm.id,
                gate_id=gate_id,
            )
            properties["gate_location_url"] = url_for(
                "web.update_farm_gate_location",
                farm_id=farm.id,
                gate_id=gate_id,
            )
    feature_collection.extend(gate_features)
    fence_features = FenceService.active_map_features_for_farm(str(farm.id))
    for feature in fence_features:
        properties = feature.get("properties", {})
        if properties.get("fence_section_id"):
            properties["fence_detail_url"] = url_for(
                "web.farm_fence_detail",
                farm_id=farm.id,
                fence_section_id=properties["fence_section_id"],
            )
    feature_collection.extend(fence_features)

    return {
        "type": "FeatureCollection",
        "farm_id": str(farm.id),
        "farm_name": farm.name,
        "kml_path": kml_path,
        "metric": "grazing_pressure_ratio",
        "generated_at": f"{datetime.utcnow().isoformat()}Z",
        "unmatched_placemarks": unmatched_placemarks,
        "paddocks_without_kml": paddocks_without_kml,
        "warnings": warnings,
        "features": feature_collection,
    }


def register_legacy_routes(bp) -> None:
    @bp.get("/farms/<farm_id>/map-data")
    def farm_map_data(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        try:
            payload = _build_farm_map_feature_collection(
                farm,
                include_water=True,
                allow_missing_kml=True,
            )
        except ET.ParseError:
            expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
            return (
                jsonify(
                    {
                        "error": "Unable to parse farm KML file",
                        "farm_id": str(farm.id),
                        "farm_name": farm.name,
                        "kml_path": str(expected_kml_path),
                    }
                ),
                500,
            )

        return jsonify(payload)

    @bp.get("/dashboard/map-data")
    def dashboard_map_data():
        farms = Farm.query.order_by(Farm.name).all()

        combined_features = []
        unmatched_placemarks = []
        paddocks_without_kml = []
        missing_kml_farms = []
        invalid_kml_farms = []
        kml_paths = []
        seen_gate_ids = set()

        for farm in farms:
            expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
            try:
                payload = _build_farm_map_feature_collection(farm)
            except FileNotFoundError:
                missing_kml_farms.append(
                    {"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)}
                )
                continue
            except ET.ParseError:
                invalid_kml_farms.append(
                    {"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)}
                )
                continue

            kml_paths.append(payload["kml_path"])
            for feature in payload["features"]:
                properties = feature.get("properties", {})
                if properties.get("feature_type") == "gate":
                    gate_id = properties.get("gate_id") or properties.get("id")
                    if gate_id:
                        if gate_id in seen_gate_ids:
                            continue
                        seen_gate_ids.add(gate_id)
                combined_features.append(feature)
            unmatched_placemarks.extend(
                [f"{farm.name}: {name}" for name in payload["unmatched_placemarks"]]
            )
            paddocks_without_kml.extend(
                [f"{farm.name}: {name}" for name in payload["paddocks_without_kml"]]
            )

        return jsonify(
            {
                "type": "FeatureCollection",
                "metric": "grazing_pressure_ratio",
                "generated_at": f"{datetime.utcnow().isoformat()}Z",
                "farm_count": len(farms),
                "mapped_farm_count": len(kml_paths),
                "kml_paths": kml_paths,
                "missing_kml_farms": missing_kml_farms,
                "invalid_kml_farms": invalid_kml_farms,
                "unmatched_placemarks": unmatched_placemarks,
                "paddocks_without_kml": paddocks_without_kml,
                "features": combined_features,
            }
        )
