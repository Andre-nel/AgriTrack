from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import xml.etree.ElementTree as ET

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    Farm,
    GrazingAllocation,
    GrazingSession,
    Mob,
    Paddock,
    RainfallRecord,
)
from app.models.stock_ledger import StockEventType
from app.services.movement_service import MovementService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService

bp = Blueprint("web", __name__)


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
    for mob in mobs:
        for balance in mob.balances:
            group = balance.animal_group_type
            key = (
                group.species,
                group.breed,
                group.sex,
                group.age_class,
            )
            stock_totals[key] = stock_totals.get(key, 0) + balance.head_count

    farm_stock_summary_all = [
        {
            "species": key[0],
            "breed": key[1],
            "sex": key[2],
            "age_class": key[3],
            "head_count": head_count,
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
    )


@bp.get("/farms/<farm_id>/map-data")
def farm_map_data(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    expected_kml_path = Path(current_app.instance_path) / "maps" / f"{farm.name}.kml"

    try:
        kml_features, kml_path = _load_farm_kml_features(farm.name)
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

    paddocks = list(Paddock.query.filter_by(farm_id=farm_id).all())
    paddock_by_name = {_normalize_name(p.name): p for p in paddocks}
    farm_name_key = _normalize_name(farm.name)
    active_snapshot = _active_grazing_snapshot_by_paddock(farm_id)

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

    return jsonify(
        {
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
    paddocks = Paddock.query.filter_by(farm_id=mob.farm_id).order_by(Paddock.name).all()
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

    return render_template(
        "mob_detail.html",
        mob=mob,
        paddocks=paddocks,
        stock_event_types=StockEventType,
        current_allocations=current_allocations,
        allocation_total_pct=float(allocation_total_pct),
        split_group_options=split_group_options,
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


@bp.post("/mobs/<mob_id>/move")
def mob_move_form(mob_id):
    mob = _get_active_mob_or_404(mob_id)
    paddock_ids = request.form.getlist("paddock_id")
    allocation_pcts = request.form.getlist("allocation_pct")

    try:
        valid_paddock_ids = {str(p.id) for p in Paddock.query.filter_by(farm_id=mob.farm_id).all()}
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
                raise ValueError("Paddock is invalid for this mob's farm")
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
        )
        db.session.commit()
        flash("Mob moved", "success")
    except (ValueError, InvalidOperation) as exc:
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
    stock = ReportingService.paddock_current_stock(paddock_id)

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
        stock=stock,
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
    )
