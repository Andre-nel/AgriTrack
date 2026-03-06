from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingSession,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
    RainfallRecord,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.mob_event_service import MobEventService
from app.services.movement_service import MovementService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService

bp = Blueprint("web", __name__)

ANALYTICS_GROUP_LABELS = {
    "species": "Species",
    "breed": "Breed",
    "sex": "Sex",
    "age_class": "Age Class",
    "farm": "Farm",
}
ANALYTICS_GROUP_ORDER = ("species", "breed", "sex", "age_class", "farm")
ANALYTICS_DEFAULT_GROUP_BY = ("species",)
ANALYTICS_STOCK_IN_TYPES = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}
ANALYTICS_STOCK_OUT_TYPES = {
    StockEventType.death,
    StockEventType.sale,
    StockEventType.missing,
    StockEventType.transfer_out,
    StockEventType.adjustment_out,
}


def _parse_paddock_lines(raw: str) -> list[dict]:
    items = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        name = parts[0]
        area = float(parts[1]) if len(parts) > 1 and parts[1] else 0
        grazeable = float(parts[2]) if len(parts) > 2 and parts[2] else area
        items.append({"name": name, "area_ha": area, "grazeable_area_ha": grazeable})
    return items


def _parse_mob_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _parse_placements(raw: str) -> list[tuple[str, str]]:
    placements = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            placements.append((parts[0], parts[1]))
    return placements


def _parse_query_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _normalize_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _round_float(value: float | None, precision: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), precision)


def _active_mobs_for_farm(farm_id: str) -> list[Mob]:
    return Mob.query.filter_by(farm_id=farm_id, status="active").order_by(Mob.name).all()


def _get_active_mob_or_404(mob_id: str) -> Mob:
    return Mob.query.filter_by(id=mob_id, status="active").first_or_404()


def _normalize_analytics_group_by(raw_values: list[str]) -> list[str]:
    selected = []
    seen = set()
    for raw in raw_values:
        value = (raw or "").strip().lower()
        if value not in ANALYTICS_GROUP_LABELS or value in seen:
            continue
        selected.append(value)
        seen.add(value)

    if not selected:
        return list(ANALYTICS_DEFAULT_GROUP_BY)
    return selected


def _stock_delta_for_event(event_type: StockEventType, quantity: int) -> int:
    if event_type in ANALYTICS_STOCK_IN_TYPES:
        return quantity
    if event_type in ANALYTICS_STOCK_OUT_TYPES:
        return -quantity
    return 0


def _stock_group_dimensions(farm_name: str, group_type: AnimalGroupType) -> dict[str, str]:
    return {
        "species": (group_type.species or "Unknown").strip(),
        "breed": (group_type.breed or "Unknown").strip(),
        "sex": (group_type.sex or "Unknown").strip(),
        "age_class": (group_type.age_class or "Unknown").strip(),
        "farm": (farm_name or "Unknown").strip(),
    }


def _stock_series_label(group_by_fields: list[str], group_key: tuple[str, ...]) -> str:
    if len(group_by_fields) == 1:
        return group_key[0]

    parts = []
    for field, value in zip(group_by_fields, group_key):
        parts.append(f"{ANALYTICS_GROUP_LABELS[field]}: {value}")
    return " | ".join(parts)


def _current_stock_totals_by_group(
    selected_filters: dict[str, str],
    group_by_fields: list[str],
) -> dict[tuple[str, ...], int]:
    rows = (
        db.session.query(AnimalGroupBalance, Mob, AnimalGroupType, Farm.name.label("farm_name"))
        .join(Mob, AnimalGroupBalance.mob_id == Mob.id)
        .join(AnimalGroupType, AnimalGroupBalance.animal_group_type_id == AnimalGroupType.id)
        .join(Farm, Mob.farm_id == Farm.id)
        .filter(Mob.status == "active", AnimalGroupBalance.head_count > 0)
    )

    if selected_filters["farm_id"]:
        rows = rows.filter(Mob.farm_id == selected_filters["farm_id"])
    if selected_filters["species"]:
        rows = rows.filter(AnimalGroupType.species == selected_filters["species"])
    if selected_filters["breed"]:
        rows = rows.filter(AnimalGroupType.breed == selected_filters["breed"])
    if selected_filters["sex"]:
        rows = rows.filter(AnimalGroupType.sex == selected_filters["sex"])
    if selected_filters["age_class"]:
        rows = rows.filter(AnimalGroupType.age_class == selected_filters["age_class"])

    totals: dict[tuple[str, ...], int] = defaultdict(int)
    for balance, _mob, group_type, farm_name in rows.all():
        dimensions = _stock_group_dimensions(farm_name=farm_name, group_type=group_type)
        group_key = tuple(dimensions[field] for field in group_by_fields)
        totals[group_key] += int(balance.head_count)

    return totals


def _build_stock_tracking_chart_data(
    ledger_rows: list[tuple[StockLedgerEntry, str, AnimalGroupType]],
    group_by_fields: list[str],
    start_date: date,
    end_date: date,
    reconcile_to_current_totals: dict[tuple[str, ...], int] | None = None,
) -> tuple[dict, list[dict]]:
    labels = []
    cursor = start_date
    while cursor <= end_date:
        labels.append(cursor.isoformat())
        cursor += timedelta(days=1)

    baseline_totals: dict[tuple[str, ...], int] = defaultdict(int)
    daily_deltas: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))

    for entry, farm_name, group_type in ledger_rows:
        event_date = entry.event_time.date()
        dimensions = _stock_group_dimensions(farm_name=farm_name, group_type=group_type)
        group_key = tuple(dimensions[field] for field in group_by_fields)
        delta = _stock_delta_for_event(event_type=entry.event_type, quantity=int(entry.quantity))
        if delta == 0:
            continue

        if event_date < start_date:
            baseline_totals[group_key] += delta
            continue
        if event_date > end_date:
            continue

        daily_deltas[event_date.isoformat()][group_key] += delta

    all_keys = set(baseline_totals.keys())
    for values in daily_deltas.values():
        all_keys.update(values.keys())
    if reconcile_to_current_totals:
        all_keys.update(reconcile_to_current_totals.keys())

    sorted_keys = sorted(all_keys, key=lambda row: tuple(value.lower() for value in row))
    running_totals = {key: baseline_totals.get(key, 0) for key in sorted_keys}
    series_by_key = {key: [] for key in sorted_keys}

    for label in labels:
        day_changes = daily_deltas.get(label, {})
        for key, delta in day_changes.items():
            running_totals[key] = running_totals.get(key, 0) + delta

        for key in sorted_keys:
            series_by_key[key].append(running_totals.get(key, 0))

    if reconcile_to_current_totals:
        for key in sorted_keys:
            desired_latest = int(reconcile_to_current_totals.get(key, 0))
            if not series_by_key[key]:
                continue
            observed_latest = int(series_by_key[key][-1])
            offset = desired_latest - observed_latest
            if offset:
                series_by_key[key] = [value + offset for value in series_by_key[key]]

    datasets = []
    latest_totals = []
    for key in sorted_keys:
        values = series_by_key[key]
        if not values or not any(value != 0 for value in values):
            continue
        label = _stock_series_label(group_by_fields=group_by_fields, group_key=key)
        datasets.append({"label": label, "values": values})
        latest_totals.append({"label": label, "head_count": values[-1]})

    latest_totals.sort(key=lambda row: (-row["head_count"], row["label"].lower()))
    return {"labels": labels, "datasets": datasets}, latest_totals


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
        GrazingAllocation.query.join(GrazingSession)
        .filter(
            GrazingSession.farm_id == farm_id,
            GrazingSession.end_at.is_(None),
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

        fraction = float(allocation.allocation_fraction)
        mob = allocation.grazing_session.mob
        allocated_lsu = ReportingService.mob_total_lsu(mob) * fraction
        snapshot["current_lsu"] += allocated_lsu
        snapshot["mobs"].append(
            {
                "mob_id": str(mob.id),
                "mob_name": mob.name,
                "allocation_pct": _round_float(fraction * 100.0, 2),
                "allocated_lsu": _round_float(allocated_lsu, 3),
            }
        )

        for balance in mob.balances:
            species = balance.animal_group_type.species
            current_head = snapshot["species_heads"].get(species, 0.0)
            snapshot["species_heads"][species] = current_head + (float(balance.head_count) * fraction)

    for snapshot in by_paddock.values():
        snapshot["mobs"].sort(key=lambda item: item["allocated_lsu"], reverse=True)
        species_rows = [
            {"species": species, "head": _round_float(head, 2)}
            for species, head in sorted(snapshot["species_heads"].items(), key=lambda row: row[0])
        ]
        snapshot["species_heads"] = species_rows
        snapshot["current_lsu"] = _round_float(snapshot["current_lsu"], 3)

    return by_paddock


def _paddock_map_properties(paddock: Paddock, farm: Farm, active_snapshot: dict) -> dict:
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

    year_lsu_days = ReportingService.paddock_lsu_days_for_period(
        paddock_id=str(paddock.id),
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
        "current_lsu": active.get("current_lsu", 0.0),
        "mobs": active.get("mobs", []),
        "species_heads": active.get("species_heads", []),
    }


def _build_farm_map_feature_collection(farm: Farm) -> dict:
    kml_features, kml_path = _load_farm_kml_features(farm.name)
    paddocks = list(Paddock.query.filter_by(farm_id=farm.id).all())
    paddock_by_name = {_normalize_name(p.name): p for p in paddocks}
    farm_name_key = _normalize_name(farm.name)
    active_snapshot = _active_grazing_snapshot_by_paddock(str(farm.id))

    feature_collection = []
    matched_paddock_ids = set()
    unmatched_placemarks = []

    for item in kml_features:
        normalized_name = item["normalized_name"]
        paddock = paddock_by_name.get(normalized_name)
        if paddock:
            properties = _paddock_map_properties(paddock, farm, active_snapshot)
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
        p.name for p in sorted(paddocks, key=lambda row: row.name.lower()) if str(p.id) not in matched_paddock_ids
    ]

    return {
        "type": "FeatureCollection",
        "farm_id": str(farm.id),
        "farm_name": farm.name,
        "kml_path": kml_path,
        "metric": "grazing_pressure_ratio",
        "generated_at": f"{datetime.utcnow().isoformat()}Z",
        "unmatched_placemarks": unmatched_placemarks,
        "paddocks_without_kml": paddocks_without_kml,
        "features": feature_collection,
    }


@bp.get("/")
def dashboard():
    summary = ReportingService.dashboard_summary()
    farms = Farm.query.order_by(Farm.name).all()

    farm_cards = []
    for farm in farms:
        paddocks = len(farm.paddocks)
        mobs = len([mob for mob in farm.mobs if mob.status == "active"])
        ready = paddocks > 0 and mobs > 0
        farm_cards.append(
            {
                "id": str(farm.id),
                "name": farm.name,
                "timezone": farm.timezone,
                "paddock_count": paddocks,
                "mob_count": mobs,
                "rainfall_count": len(farm.rainfall_records),
                "ready": ready,
            }
        )

    return render_template("dashboard.html", summary=summary, farm_cards=farm_cards)


@bp.get("/analytics")
def analytics_landing():
    return render_template("analytics/index.html")


@bp.get("/analytics/stock-tracking")
def analytics_stock_tracking():
    selected_filters = {
        "farm_id": (request.args.get("farm_id") or "").strip(),
        "species": (request.args.get("species") or "").strip(),
        "breed": (request.args.get("breed") or "").strip(),
        "sex": (request.args.get("sex") or "").strip(),
        "age_class": (request.args.get("age_class") or "").strip(),
    }
    selected_group_by = _normalize_analytics_group_by(request.args.getlist("group_by"))
    today = date.today()

    try:
        start_date = _parse_query_date(request.args.get("start_date"))
        end_date = _parse_query_date(request.args.get("end_date")) or today
    except ValueError:
        flash("Analytics dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.analytics_stock_tracking"))

    if start_date and end_date < start_date:
        flash("Analytics end date must be on or after the start date", "error")
        return redirect(url_for("web.analytics_stock_tracking"))

    farms = Farm.query.order_by(Farm.name).all()
    group_types = AnimalGroupType.query.order_by(
        AnimalGroupType.species,
        AnimalGroupType.breed,
        AnimalGroupType.sex,
        AnimalGroupType.age_class,
    ).all()
    filter_options = {
        "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
        "species": sorted({group.species for group in group_types}),
        "breed": sorted({group.breed for group in group_types}),
        "sex": sorted({group.sex for group in group_types}),
        "age_class": sorted({group.age_class for group in group_types}),
    }
    group_by_options = [
        {"value": field, "label": ANALYTICS_GROUP_LABELS[field]}
        for field in ANALYTICS_GROUP_ORDER
    ]

    ledger_query = (
        db.session.query(StockLedgerEntry, Farm.name.label("farm_name"), AnimalGroupType)
        .join(Farm, StockLedgerEntry.farm_id == Farm.id)
        .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
    )

    if selected_filters["farm_id"]:
        ledger_query = ledger_query.filter(StockLedgerEntry.farm_id == selected_filters["farm_id"])
    if selected_filters["species"]:
        ledger_query = ledger_query.filter(AnimalGroupType.species == selected_filters["species"])
    if selected_filters["breed"]:
        ledger_query = ledger_query.filter(AnimalGroupType.breed == selected_filters["breed"])
    if selected_filters["sex"]:
        ledger_query = ledger_query.filter(AnimalGroupType.sex == selected_filters["sex"])
    if selected_filters["age_class"]:
        ledger_query = ledger_query.filter(AnimalGroupType.age_class == selected_filters["age_class"])

    period_end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    ledger_rows = (
        ledger_query.filter(StockLedgerEntry.event_time < period_end_dt)
        .order_by(StockLedgerEntry.event_time.asc(), StockLedgerEntry.id.asc())
        .all()
    )

    if start_date is None:
        if ledger_rows:
            start_date = ledger_rows[0][0].event_time.date()
        else:
            start_date = end_date - timedelta(days=30)

    reconcile_to_current_totals = None
    if end_date >= today:
        current_totals = _current_stock_totals_by_group(
            selected_filters=selected_filters,
            group_by_fields=selected_group_by,
        )
        if current_totals:
            reconcile_to_current_totals = current_totals

    chart_data, latest_totals = _build_stock_tracking_chart_data(
        ledger_rows=ledger_rows,
        group_by_fields=selected_group_by,
        start_date=start_date,
        end_date=end_date,
        reconcile_to_current_totals=reconcile_to_current_totals,
    )

    return render_template(
        "analytics/stock_tracking.html",
        filter_options=filter_options,
        selected_filters=selected_filters,
        group_by_options=group_by_options,
        selected_group_by=selected_group_by,
        start_date=start_date,
        end_date=end_date,
        chart_data=chart_data,
        latest_totals=latest_totals,
    )


@bp.route("/setup", methods=["GET", "POST"])
def setup_farm():
    if request.method == "GET":
        return render_template("setup.html")

    farm_name = (request.form.get("farm_name") or "").strip()
    timezone = (request.form.get("timezone") or "UTC").strip() or "UTC"
    paddocks_raw = request.form.get("paddocks") or ""
    mobs_raw = request.form.get("mobs") or ""
    placements_raw = request.form.get("placements") or ""

    if not farm_name:
        flash("Farm name is required", "error")
        return redirect(url_for("web.setup_farm"))

    try:
        farm = Farm(name=farm_name, timezone=timezone, active=True)
        db.session.add(farm)
        db.session.flush()

        paddock_map = {}
        mob_map = {}

        for p in _parse_paddock_lines(paddocks_raw):
            paddock = Paddock(
                farm_id=farm.id,
                name=p["name"],
                area_ha=p["area_ha"],
                grazeable_area_ha=p["grazeable_area_ha"],
            )
            db.session.add(paddock)
            db.session.flush()
            paddock_map[paddock.name.lower()] = paddock

        for name in _parse_mob_lines(mobs_raw):
            mob = Mob(farm_id=farm.id, name=name, status="active")
            db.session.add(mob)
            db.session.flush()
            mob_map[mob.name.lower()] = mob

        for mob_name, paddock_name in _parse_placements(placements_raw):
            mob = mob_map.get(mob_name.lower())
            paddock = paddock_map.get(paddock_name.lower())
            if not mob or not paddock:
                continue
            MovementService.move_mob(
                mob=mob,
                allocations=[{"paddock_id": paddock.id, "allocation_fraction": "1.0"}],
            )

        db.session.commit()
        flash("Farm setup complete", "success")
        return redirect(url_for("web.farm_detail", farm_id=farm.id))
    except (ValueError, TypeError) as exc:
        db.session.rollback()
        flash(f"Setup failed: {exc}", "error")
        return redirect(url_for("web.setup_farm"))


@bp.route("/farms", methods=["GET", "POST"])
def farms_page():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        timezone = (request.form.get("timezone") or "UTC").strip() or "UTC"
        if not name:
            flash("Farm name is required", "error")
            return redirect(url_for("web.farms_page"))

        db.session.add(Farm(name=name, timezone=timezone, active=True))
        db.session.commit()
        flash("Farm created", "success")
        return redirect(url_for("web.farms_page"))

    farms = Farm.query.order_by(Farm.name).all()
    return render_template("farms.html", farms=farms)


@bp.get("/farms/<farm_id>")
def farm_detail(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    paddocks = sorted(farm.paddocks, key=lambda p: p.name.lower())
    mobs = _active_mobs_for_farm(farm_id)
    located_mob_ids = set()
    if mobs:
        mob_ids = [mob.id for mob in mobs]
        active_allocations = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingSession.mob_id.in_(mob_ids),
                GrazingSession.end_at.is_(None),
            )
            .all()
        )
        located_mob_ids = {str(allocation.grazing_session.mob_id) for allocation in active_allocations}
    mob_detail_labels = {
        str(mob.id): ("Not Located" if str(mob.id) not in located_mob_ids else mob.status)
        for mob in mobs
    }
    farm_total_area_ha = float(sum((p.area_ha or 0) for p in paddocks))
    farm_current_lsu = sum(ReportingService.mob_total_lsu(mob) for mob in mobs)
    farm_capacity_lsu = (
        farm_total_area_ha / float(farm.default_stocking_rate_ha_per_lsu)
        if float(farm.default_stocking_rate_ha_per_lsu) > 0
        else 0.0
    )
    selected_filters = {
        "species": (request.args.get("species") or "").strip(),
        "breed": (request.args.get("breed") or "").strip(),
        "sex": (request.args.get("sex") or "").strip(),
        "age_class": (request.args.get("age_class") or "").strip(),
    }
    stock_totals = {}
    stock_mob_totals = {}
    for mob in mobs:
        mob_id = str(mob.id)
        for balance in mob.balances:
            group = balance.animal_group_type
            key = (
                group.species,
                group.breed,
                group.sex,
                group.age_class,
            )
            head_count = int(balance.head_count)
            stock_totals[key] = stock_totals.get(key, 0) + head_count

            mob_totals = stock_mob_totals.setdefault(key, {})
            mob_totals[mob_id] = {
                "mob_id": mob_id,
                "mob_name": mob.name,
                "head_count": mob_totals.get(mob_id, {}).get("head_count", 0) + head_count,
            }

    farm_stock_summary_all = [
        {
            "species": key[0],
            "breed": key[1],
            "sex": key[2],
            "age_class": key[3],
            "head_count": head_count,
            "mobs": sorted(
                stock_mob_totals.get(key, {}).values(),
                key=lambda row: (-row["head_count"], row["mob_name"].lower()),
            ),
        }
        for key, head_count in sorted(stock_totals.items(), key=lambda item: item[0])
    ]
    filter_options = {
        "species": sorted({row["species"] for row in farm_stock_summary_all}),
        "breed": sorted({row["breed"] for row in farm_stock_summary_all}),
        "sex": sorted({row["sex"] for row in farm_stock_summary_all}),
        "age_class": sorted({row["age_class"] for row in farm_stock_summary_all}),
    }
    farm_stock_summary = [
        row
        for row in farm_stock_summary_all
        if (not selected_filters["species"] or row["species"] == selected_filters["species"])
        and (not selected_filters["breed"] or row["breed"] == selected_filters["breed"])
        and (not selected_filters["sex"] or row["sex"] == selected_filters["sex"])
        and (not selected_filters["age_class"] or row["age_class"] == selected_filters["age_class"])
    ]
    farm_stock_total = sum(row["head_count"] for row in farm_stock_summary)

    rainfall = (
        RainfallRecord.query.filter_by(farm_id=farm.id)
        .order_by(RainfallRecord.recorded_on.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "farm_detail.html",
        farm=farm,
        paddocks=paddocks,
        mobs=mobs,
        rainfall=rainfall,
        farm_stock_summary=farm_stock_summary,
        farm_stock_total=farm_stock_total,
        stock_filter_options=filter_options,
        selected_stock_filters=selected_filters,
        farm_total_area_ha=farm_total_area_ha,
        farm_capacity_lsu=farm_capacity_lsu,
        farm_current_lsu=farm_current_lsu,
        mob_detail_labels=mob_detail_labels,
    )


@bp.get("/farms/<farm_id>/map-data")
def farm_map_data(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    try:
        payload = _build_farm_map_feature_collection(farm)
    except FileNotFoundError as exc:
        return (
            jsonify(
                {
                    "error": "Farm map KML file not found",
                    "farm_id": str(farm.id),
                    "farm_name": farm.name,
                    "expected_path": str(exc.args[0]),
                }
            ),
            404,
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

    for farm in farms:
        expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"
        try:
            payload = _build_farm_map_feature_collection(farm)
        except FileNotFoundError:
            missing_kml_farms.append({"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)})
            continue
        except ET.ParseError:
            invalid_kml_farms.append({"farm_id": str(farm.id), "farm_name": farm.name, "path": str(expected_kml_path)})
            continue

        kml_paths.append(payload["kml_path"])
        combined_features.extend(payload["features"])
        unmatched_placemarks.extend([f"{farm.name}: {name}" for name in payload["unmatched_placemarks"]])
        paddocks_without_kml.extend([f"{farm.name}: {name}" for name in payload["paddocks_without_kml"]])

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


@bp.post("/farms/<farm_id>/stocking-rate")
def update_farm_stocking_rate_form(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    value_raw = (request.form.get("default_stocking_rate_ha_per_lsu") or "").strip()
    try:
        value = Decimal(value_raw)
        if value <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Farm carrying capacity (ha/LSU) must be greater than 0", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    farm.default_stocking_rate_ha_per_lsu = value
    db.session.commit()
    flash("Farm carrying capacity updated", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/paddocks")
def create_paddock_form(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    name = (request.form.get("name") or "").strip()
    override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
    if not name:
        flash("Paddock name is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    override_value = None
    if override_raw:
        try:
            override_value = Decimal(override_raw)
            if override_value <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Paddock carrying capacity override (ha/LSU) must be greater than 0", "error")
            return redirect(url_for("web.farm_detail", farm_id=farm_id))

    paddock = Paddock(
        farm_id=farm_id,
        name=name,
        area_ha=request.form.get("area_ha") or 0,
        grazeable_area_ha=request.form.get("grazeable_area_ha") or 0,
        stocking_rate_ha_per_lsu_override=override_value,
    )
    db.session.add(paddock)
    db.session.commit()
    flash(f"Paddock created for {farm.name}", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/mobs")
def create_mob_form(farm_id):
    Farm.query.get_or_404(farm_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Mob name is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    mob = Mob(farm_id=farm_id, name=name, status="active")
    db.session.add(mob)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Active mob name already exists on this farm", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))
    flash("Mob created", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/rainfall")
def create_rainfall_form(farm_id):
    Farm.query.get_or_404(farm_id)
    recorded_on_raw = (request.form.get("recorded_on") or "").strip()
    mm = request.form.get("mm")
    if not mm:
        flash("Rainfall mm is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    try:
        recorded_on = date.fromisoformat(recorded_on_raw) if recorded_on_raw else date.today()
    except ValueError:
        flash("Recorded on must be a valid date", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    try:
        row = RainfallRecord(farm_id=farm_id, recorded_on=recorded_on, mm=mm, source="manual")
        db.session.add(row)
        db.session.commit()
        flash("Rainfall record added", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.get("/mobs/<mob_id>")
def mob_detail(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    selected_event_tag = " ".join((request.args.get("event_tag") or "").strip().lower().split())
    farms = Farm.query.order_by(Farm.name).all()
    all_paddocks = Paddock.query.order_by(Paddock.name).all()
    all_active_mobs = Mob.query.filter_by(status="active").order_by(Mob.name).all()
    move_farms = [{"id": str(farm.id), "name": farm.name} for farm in farms]
    move_paddocks_by_farm = {str(farm.id): [] for farm in farms}
    transfer_mobs_by_farm = {str(farm.id): [] for farm in farms}
    for paddock in all_paddocks:
        move_paddocks_by_farm.setdefault(str(paddock.farm_id), []).append(
            {"id": str(paddock.id), "name": paddock.name}
        )
    for item in all_active_mobs:
        if str(item.id) == str(mob.id):
            continue
        transfer_mobs_by_farm.setdefault(str(item.farm_id), []).append(
            {"id": str(item.id), "name": item.name}
        )
    has_move_paddocks = any(len(rows) > 0 for rows in move_paddocks_by_farm.values())
    has_transfer_destination_mobs = any(len(rows) > 0 for rows in transfer_mobs_by_farm.values())
    default_move_farm_id = str(mob.farm_id)
    default_transfer_farm_id = str(mob.farm_id)
    active_session = next((session for session in mob.grazing_sessions if session.end_at is None), None)
    current_allocations = []
    allocation_total_pct = Decimal("0")
    if active_session:
        for allocation in sorted(active_session.allocations, key=lambda a: a.paddock.name.lower()):
            pct = Decimal(str(allocation.allocation_fraction)) * Decimal("100")
            allocation_total_pct += pct
            current_allocations.append(
                {
                    "paddock_name": allocation.paddock.name,
                    "allocation_pct": float(pct),
                }
            )
    split_group_options = []
    for balance in sorted(
        mob.balances,
        key=lambda b: (
            b.animal_group_type.species.lower(),
            b.animal_group_type.breed.lower(),
            b.animal_group_type.sex.lower(),
            b.animal_group_type.age_class.lower(),
        ),
    ):
        if int(balance.head_count) <= 0:
            continue
        group = balance.animal_group_type
        split_group_options.append(
            {
                "id": str(balance.animal_group_type_id),
                "species": group.species,
                "breed": group.breed,
                "sex": group.sex,
                "age_class": group.age_class,
                "head_count": int(balance.head_count),
                "label": (
                    f"{group.species} | {group.breed} | {group.sex} | {group.age_class} "
                    f"(available: {int(balance.head_count)})"
                ),
            }
        )

    mob_events_all = []
    event_rows = (
        MobEvent.query.filter_by(mob_id=mob.id)
        .order_by(MobEvent.event_at.desc(), MobEvent.created_at.desc())
        .all()
    )
    for event in event_rows:
        tags = MobEventService.tags_from_csv(event.tags_csv)
        mob_events_all.append(
            {
                "id": str(event.id),
                "event_at": event.event_at,
                "tags": tags,
                "description": event.description,
            }
        )
    event_tag_options = sorted({tag for row in mob_events_all for tag in row["tags"]})
    mob_events = [
        row
        for row in mob_events_all
        if not selected_event_tag or selected_event_tag in row["tags"]
    ]

    return render_template(
        "mob_detail.html",
        mob=mob,
        move_farms=move_farms,
        move_paddocks_by_farm=move_paddocks_by_farm,
        has_move_paddocks=has_move_paddocks,
        default_move_farm_id=default_move_farm_id,
        transfer_mobs_by_farm=transfer_mobs_by_farm,
        has_transfer_destination_mobs=has_transfer_destination_mobs,
        default_transfer_farm_id=default_transfer_farm_id,
        stock_event_types=StockEventType,
        current_allocations=current_allocations,
        allocation_total_pct=float(allocation_total_pct),
        split_group_options=split_group_options,
        mob_events=mob_events,
        event_tag_options=event_tag_options,
        selected_event_tag=selected_event_tag,
    )


@bp.post("/mobs/<mob_id>/adjust")
def mob_adjust_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    try:
        group_type = StockService.get_or_create_group_type(
            species=(request.form.get("species") or "Cattle").strip(),
            breed=(request.form.get("breed") or "mixed").strip(),
            sex=(request.form.get("sex") or "mixed").strip(),
            age_class=(request.form.get("age_class") or "adult").strip(),
        )
        selected_event_type = StockEventType(request.form.get("event_type") or "count")
        quantity = int(request.form.get("quantity") or 0)

        event_type = selected_event_type
        event_quantity = quantity
        if selected_event_type == StockEventType.count:
            current_balance = (
                AnimalGroupBalance.query.filter_by(
                    mob_id=mob.id,
                    animal_group_type_id=group_type.id,
                ).first()
            )
            current_head_count = current_balance.head_count if current_balance else 0
            delta = quantity - current_head_count
            if delta == 0:
                flash("Count matches current balance. No stock adjustment posted.", "success")
                return redirect(url_for("web.mob_detail", mob_id=mob_id))
            if delta > 0:
                event_type = StockEventType.adjustment_in
                event_quantity = delta
            else:
                event_type = StockEventType.missing
                event_quantity = -delta

        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=group_type.id,
            event_type=event_type,
            quantity=event_quantity,
            note=request.form.get("note"),
        )
        db.session.commit()
        flash("Stock updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/balances/edit")
def mob_edit_balance_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    source_group_type_id = (request.form.get("source_animal_group_type_id") or "").strip()
    if not source_group_type_id:
        flash("Select a balance line to edit", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    source_balance = AnimalGroupBalance.query.filter_by(
        mob_id=mob.id,
        animal_group_type_id=source_group_type_id,
    ).first()
    if not source_balance or int(source_balance.head_count) <= 0:
        flash("Selected balance line is no longer available. Refresh and try again.", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    source_group = source_balance.animal_group_type
    source_head = int(source_balance.head_count)
    head_count_raw = (request.form.get("head_count") or "").strip()
    try:
        target_head = int(head_count_raw)
    except ValueError:
        flash("Head count must be a whole number", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    if target_head <= 0:
        flash("Head count must be greater than 0", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    note_text = " ".join((request.form.get("note") or "").strip().split())

    try:
        target_group = StockService.get_or_create_group_type(
            species=source_group.species,
            breed=source_group.breed,
            sex=(request.form.get("sex") or source_group.sex).strip(),
            age_class=(request.form.get("age_class") or source_group.age_class).strip(),
        )

        source_label = (
            f"{source_group.species} | {source_group.breed} | "
            f"{source_group.sex} | {source_group.age_class}"
        )
        target_label = (
            f"{target_group.species} | {target_group.breed} | "
            f"{target_group.sex} | {target_group.age_class}"
        )
        unchanged = str(target_group.id) == str(source_group.id) and target_head == source_head
        if unchanged:
            flash("No changes detected for the selected balance line", "success")
            return redirect(url_for("web.mob_detail", mob_id=mob_id))

        if str(target_group.id) == str(source_group.id):
            delta = target_head - source_head
            event_type = StockEventType.adjustment_in if delta > 0 else StockEventType.adjustment_out
            quantity = abs(delta)
            description = (
                f"Balance head updated for {source_label}: {source_head} -> {target_head}."
            )
            event_tags = "stock,balance edit,head adjustment"
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=source_group.id,
                event_type=event_type,
                quantity=quantity,
                note=f"{description} Note: {note_text}" if note_text else description,
            )
        else:
            description = (
                f"Balance reclassified from {source_label} (head: {source_head}) "
                f"to {target_label} (head: {target_head})."
            )
            event_tags = "stock,balance edit,reclassification"
            ledger_note = f"{description} Note: {note_text}" if note_text else description
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=source_group.id,
                event_type=StockEventType.adjustment_out,
                quantity=source_head,
                note=ledger_note,
            )
            StockService.adjust_stock(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                animal_group_type_id=target_group.id,
                event_type=StockEventType.adjustment_in,
                quantity=target_head,
                note=ledger_note,
            )

        event_description = f"{description} Note: {note_text}" if note_text else description
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=event_description,
            raw_tags=event_tags,
        )
        db.session.commit()
        flash("Balance line updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/events")
def mob_event_create_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    tags_text = request.form.get("event_tags")
    description = request.form.get("event_description")

    try:
        MobEventService.create_event(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            description=description,
            raw_tags=tags_text,
        )
        db.session.commit()
        flash("Mob event recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/rename")
def mob_rename_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Mob name is required", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    if name == mob.name:
        flash("Mob name unchanged", "success")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    mob.name = name
    try:
        db.session.commit()
        flash("Mob name updated", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Active mob name already exists on this farm", "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/move")
def mob_move_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    destination_farm_id = (request.form.get("destination_farm_id") or str(mob.farm_id)).strip()
    paddock_ids = request.form.getlist("paddock_id")
    allocation_pcts = request.form.getlist("allocation_pct")

    try:
        if not destination_farm_id:
            raise ValueError("Destination farm is required")
        if not Farm.query.filter_by(id=destination_farm_id).first():
            raise ValueError("Destination farm is invalid")

        valid_paddock_ids = {
            str(p.id) for p in Paddock.query.filter_by(farm_id=destination_farm_id).all()
        }
        allocations = []
        used_paddocks = set()
        total_pct = Decimal("0")

        for paddock_id_raw, pct_raw in zip(paddock_ids, allocation_pcts):
            paddock_id = (paddock_id_raw or "").strip()
            pct_text = (pct_raw or "").strip()
            if not paddock_id and not pct_text:
                continue
            if not paddock_id:
                raise ValueError("Each allocation row requires a paddock")
            if paddock_id not in valid_paddock_ids:
                raise ValueError("Selected paddock is invalid for the chosen destination farm")
            if paddock_id in used_paddocks:
                raise ValueError("Duplicate paddock rows are not allowed")
            if not pct_text:
                raise ValueError("Each allocation row requires a percentage")

            pct = Decimal(pct_text)
            if pct <= 0:
                raise ValueError("Allocation percentages must be greater than 0")
            if pct > 100:
                raise ValueError("Allocation percentages cannot exceed 100")

            used_paddocks.add(paddock_id)
            total_pct += pct
            fraction = pct / Decimal("100")
            allocations.append(
                {
                    "paddock_id": paddock_id,
                    "allocation_fraction": str(fraction),
                }
            )

        if not allocations:
            raise ValueError("At least one paddock allocation is required")
        if total_pct != Decimal("100"):
            raise ValueError("Allocation percentages must add up to 100")

        MovementService.move_mob(
            mob=mob,
            allocations=allocations,
            destination_farm_id=destination_farm_id,
        )
        db.session.commit()
        flash("Mob moved", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Move failed: active mob name already exists on the destination farm", "error")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/transfer")
def mob_transfer_form(mob_id):
    source = _get_active_mob_or_404(mob_id)
    destination_farm_id = (request.form.get("transfer_destination_farm_id") or str(source.farm_id)).strip()
    destination_mob_id = (request.form.get("transfer_destination_mob_id") or "").strip()
    transfer_group_ids = request.form.getlist("transfer_group_id")
    transfer_quantities = request.form.getlist("transfer_quantity")
    note = (request.form.get("transfer_note") or "").strip() or None

    try:
        if not destination_farm_id:
            raise ValueError("Destination farm is required")
        if not Farm.query.filter_by(id=destination_farm_id).first():
            raise ValueError("Destination farm is invalid")
        if not destination_mob_id:
            raise ValueError("Destination mob is required")

        destination_mob = Mob.query.filter_by(id=destination_mob_id, status="active").first()
        if not destination_mob:
            raise ValueError("Destination mob is invalid")
        if str(destination_mob.farm_id) != destination_farm_id:
            raise ValueError("Destination mob is invalid for the selected farm")

        transfer_totals: dict[str, int] = {}
        for group_id_raw, qty_raw in zip(transfer_group_ids, transfer_quantities):
            group_id = (group_id_raw or "").strip()
            qty_text = (qty_raw or "").strip()

            if not group_id and not qty_text:
                continue
            if not group_id or not qty_text:
                raise ValueError("Each transfer row requires a group and quantity")

            try:
                quantity = int(qty_text)
            except ValueError:
                raise ValueError("Transfer quantities must be whole numbers")
            if quantity <= 0:
                raise ValueError("Transfer quantities must be greater than 0")

            transfer_totals[group_id] = transfer_totals.get(group_id, 0) + quantity

        if not transfer_totals:
            raise ValueError("Add at least one transfer row")

        transfers = [
            {"animal_group_type_id": group_id, "quantity": quantity}
            for group_id, quantity in transfer_totals.items()
        ]
        MovementService.transfer_stock_between_mobs(
            source_mob=source,
            destination_mob=destination_mob,
            transfers=transfers,
            destination_farm_id=destination_farm_id,
            note=note,
        )
        db.session.commit()
        flash(f"Transferred stock to {destination_mob.name}", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Transfer failed due to a concurrent stock update. Please retry.", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/mobs/<mob_id>/deactivate")
def mob_deactivate_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    total_head_count = sum(int(balance.head_count) for balance in mob.balances)
    if total_head_count > 0:
        flash("Mob cannot be deactivated while it still contains stock", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    mob.status = "archived"
    db.session.commit()
    flash("Mob deactivated", "success")
    return redirect(url_for("web.farm_detail", farm_id=mob.farm_id))


@bp.post("/mobs/<mob_id>/split")
def mob_split_form(mob_id):
    source = _get_active_mob_or_404(mob_id)
    split_names = request.form.getlist("split_mob_name")
    split_group_ids = request.form.getlist("split_group_id")
    split_quantities = request.form.getlist("split_quantity")

    try:
        split_map: dict[str, dict[str, int]] = {}

        for name_raw, group_id_raw, qty_raw in zip(split_names, split_group_ids, split_quantities):
            name = (name_raw or "").strip()
            group_id = (group_id_raw or "").strip()
            qty_text = (qty_raw or "").strip()

            if not name and not group_id and not qty_text:
                continue
            if not name or not group_id or not qty_text:
                raise ValueError("Each split row requires a mob name, group, and quantity")

            try:
                qty = int(qty_text)
            except ValueError:
                raise ValueError("Split quantities must be whole numbers")
            if qty <= 0:
                raise ValueError("Split quantities must be greater than 0")

            split_map.setdefault(name, {})
            split_map[name][group_id] = split_map[name].get(group_id, 0) + qty

        if not split_map:
            raise ValueError("Add at least one split allocation row")

        splits = [
            {
                "name": name,
                "groups": [
                    {"animal_group_type_id": group_id, "quantity": quantity}
                    for group_id, quantity in group_totals.items()
                ],
            }
            for name, group_totals in split_map.items()
        ]

        created = MovementService.split_mob(source_mob=source, splits=splits)
        db.session.commit()
        flash(f"Mob split complete. Created {len(created)} mobs.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Split failed: one or more new mob names already exist on this farm", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.post("/paddocks/<paddock_id>/move-mobs")
def paddock_move_all_mobs_form(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    mob_ids = request.form.getlist("mob_id")
    destination_farm_ids = request.form.getlist("destination_farm_id")
    destination_paddock_ids = request.form.getlist("destination_paddock_id")

    try:
        if not mob_ids:
            raise ValueError("No active mobs on this paddock to move")
        if not (
            len(mob_ids) == len(destination_farm_ids) == len(destination_paddock_ids)
        ):
            raise ValueError("Move request is incomplete. Provide destination farm and paddock for each mob")
        if len(set(mob_ids)) != len(mob_ids):
            raise ValueError("Duplicate mob rows are not allowed")

        active_allocations = (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
            )
            .all()
        )
        active_mob_ids = {
            str(allocation.grazing_session.mob_id)
            for allocation in active_allocations
            if allocation.grazing_session.mob.status == "active"
        }
        if set(mob_ids) != active_mob_ids:
            raise ValueError("Active mob list is out of date. Refresh and try again.")

        moved_count = 0
        for mob_id_raw, destination_farm_raw, destination_paddock_raw in zip(
            mob_ids, destination_farm_ids, destination_paddock_ids
        ):
            mob_id = (mob_id_raw or "").strip()
            destination_farm_id = (destination_farm_raw or "").strip()
            destination_paddock_id = (destination_paddock_raw or "").strip()

            if not destination_farm_id:
                raise ValueError("Destination farm is required for each mob")
            if not destination_paddock_id:
                raise ValueError("Destination paddock is required for each mob")
            if destination_paddock_id == str(paddock.id):
                raise ValueError("Destination paddock must be different from the current paddock")
            if mob_id not in active_mob_ids:
                raise ValueError("One or more mobs are no longer active on this paddock")

            mob = Mob.query.filter_by(id=mob_id, status="active").first()
            if not mob:
                raise ValueError("One or more selected mobs are invalid")

            MovementService.move_mob(
                mob=mob,
                allocations=[{"paddock_id": destination_paddock_id, "allocation_fraction": "1.0"}],
                destination_farm_id=destination_farm_id,
            )
            moved_count += 1

        db.session.commit()
        flash(f"Moved {moved_count} mob(s) from {paddock.name}", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Move failed: active mob name already exists on one of the destination farms", "error")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))


@bp.post("/paddocks/<paddock_id>/stocking-rate")
def update_paddock_stocking_rate_form(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
    if not override_raw:
        paddock.stocking_rate_ha_per_lsu_override = None
        db.session.commit()
        flash("Paddock carrying capacity override cleared. Farm default now applies.", "success")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    try:
        override_value = Decimal(override_raw)
        if override_value <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        flash("Paddock carrying capacity override (ha/LSU) must be greater than 0", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    paddock.stocking_rate_ha_per_lsu_override = override_value
    db.session.commit()
    flash("Paddock carrying capacity override updated", "success")
    return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))


@bp.get("/paddocks/<paddock_id>")
def paddock_detail(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    stock_summary = ReportingService.paddock_current_stock_summary(paddock_id)
    farms = Farm.query.order_by(Farm.name).all()
    all_paddocks = Paddock.query.order_by(Paddock.name).all()
    bulk_move_farms = [{"id": str(farm.id), "name": farm.name} for farm in farms]
    bulk_move_paddocks_by_farm = {str(farm.id): [] for farm in farms}
    for option_paddock in all_paddocks:
        bulk_move_paddocks_by_farm.setdefault(str(option_paddock.farm_id), []).append(
            {"id": str(option_paddock.id), "name": option_paddock.name}
        )

    today = date.today()
    try:
        period_start_date = _parse_query_date(request.args.get("period_start")) or date(today.year, 1, 1)
        period_end_date = _parse_query_date(request.args.get("period_end")) or today
    except ValueError:
        flash("Analysis dates must be valid (YYYY-MM-DD)", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    if period_end_date < period_start_date:
        flash("Analysis end date must be on or after the start date", "error")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    period_start_dt = datetime.combine(period_start_date, time.min)
    period_end_dt = datetime.combine(period_end_date + timedelta(days=1), time.min)
    current_dt = datetime.utcnow()
    this_year_start_dt = datetime(today.year, 1, 1)

    grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
    area_ha = float(paddock.area_ha or 0)
    effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha

    farm_stocking_rate = float(paddock.farm.default_stocking_rate_ha_per_lsu)
    paddock_override_rate = (
        float(paddock.stocking_rate_ha_per_lsu_override)
        if paddock.stocking_rate_ha_per_lsu_override is not None
        else None
    )
    effective_stocking_rate = paddock_override_rate or farm_stocking_rate
    grazing_capacity_sdh = (365.0 / effective_stocking_rate) if effective_stocking_rate > 0 else 0.0

    current_lsu = ReportingService.paddock_current_lsu_breakdown(paddock_id)
    current_stocking_density = (
        current_lsu["total_lsu"] / effective_area_ha if effective_area_ha > 0 else None
    )

    year_lsu_days = ReportingService.paddock_lsu_days_for_period(
        paddock_id=paddock_id,
        period_start=this_year_start_dt,
        period_end=current_dt,
    )
    sdh_used_this_year = (year_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    sdh_remaining_this_year = (
        grazing_capacity_sdh - sdh_used_this_year if sdh_used_this_year is not None else None
    )

    period_lsu_days = ReportingService.paddock_lsu_days_for_period(
        paddock_id=paddock_id,
        period_start=period_start_dt,
        period_end=period_end_dt,
    )
    period_days = (period_end_dt - period_start_dt).total_seconds() / 86400.0
    period_sdh_used = (period_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
    period_avg_stocking_density = (
        (period_lsu_days / (effective_area_ha * period_days))
        if effective_area_ha > 0 and period_days > 0
        else None
    )

    history_allocations = sorted(
        paddock.grazing_allocations,
        key=lambda a: a.grazing_session.start_at,
        reverse=True,
    )
    active_allocations = (
        GrazingAllocation.query.join(GrazingSession)
        .filter(
            GrazingAllocation.paddock_id == paddock_id,
            GrazingSession.end_at.is_(None),
        )
        .all()
    )
    bulk_move_mob_rows = []
    seen_mob_ids = set()
    for allocation in sorted(active_allocations, key=lambda row: row.grazing_session.mob.name.lower()):
        session = allocation.grazing_session
        mob = session.mob
        mob_id = str(mob.id)
        if mob.status != "active" or mob_id in seen_mob_ids:
            continue
        seen_mob_ids.add(mob_id)
        bulk_move_mob_rows.append(
            {
                "mob_id": mob_id,
                "mob_name": mob.name,
                "allocation_pct": float(allocation.allocation_fraction) * 100.0,
                "default_destination_farm_id": str(mob.farm_id),
            }
        )

    history = []
    for allocation in history_allocations:
        session = allocation.grazing_session
        allocated_lsu = ReportingService.allocation_lsu(allocation)
        history.append(
            {
                "allocation": allocation,
                "allocated_lsu": allocated_lsu,
                "mob_name": session.mob.name,
            }
        )

    return render_template(
        "paddock_detail.html",
        paddock=paddock,
        stock_summary=stock_summary,
        history=history,
        current_lsu=current_lsu,
        effective_stocking_rate=effective_stocking_rate,
        farm_stocking_rate=farm_stocking_rate,
        paddock_override_rate=paddock_override_rate,
        grazing_capacity_sdh=grazing_capacity_sdh,
        sdh_used_this_year=sdh_used_this_year,
        sdh_remaining_this_year=sdh_remaining_this_year,
        current_stocking_density=current_stocking_density,
        period_start=period_start_date,
        period_end=period_end_date,
        period_sdh_used=period_sdh_used,
        period_avg_stocking_density=period_avg_stocking_density,
        period_lsu_days=period_lsu_days,
        effective_area_ha=effective_area_ha,
        bulk_move_farms=bulk_move_farms,
        bulk_move_paddocks_by_farm=bulk_move_paddocks_by_farm,
        bulk_move_mob_rows=bulk_move_mob_rows,
    )
