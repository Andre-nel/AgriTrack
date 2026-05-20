from datetime import date
from decimal import Decimal, InvalidOperation

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, GrazingAllocation, GrazingSession, Mob, Paddock, RainfallRecord
from app.modules.farms.forms import parse_mob_lines, parse_paddock_lines, parse_placements
from app.services.farm_deletion_service import FarmDeletionService
from app.services.movement_service import MovementService
from app.services.paddock_service import PaddockService
from app.services.reporting_service import ReportingService
from app.services.water_network_service import WaterNetworkService


def _active_mobs_for_farm(farm_id: str) -> list[Mob]:
    return Mob.query.filter_by(farm_id=farm_id, status="active").order_by(Mob.name).all()


def register_legacy_routes(bp) -> None:
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

            for p in parse_paddock_lines(paddocks_raw):
                paddock_name = PaddockService.validate_available_name(str(farm.id), p["name"])
                paddock = Paddock(
                    farm_id=farm.id,
                    name=paddock_name,
                    area_ha=p["area_ha"],
                    grazeable_area_ha=p["grazeable_area_ha"],
                )
                db.session.add(paddock)
                db.session.flush()
                paddock_map[paddock.name.lower()] = paddock

            for name in parse_mob_lines(mobs_raw):
                mob = Mob(farm_id=farm.id, name=name, status="active")
                db.session.add(mob)
                db.session.flush()
                mob_map[mob.name.lower()] = mob

            for mob_name, paddock_name in parse_placements(placements_raw):
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

    @bp.post("/farms/<farm_id>/delete")
    def delete_farm_form(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        farm_name = farm.name

        try:
            FarmDeletionService.delete_farm_records(farm)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Unable to delete farm because related records still exist", "error")
            return redirect(url_for("web.farms_page"))

        try:
            FarmDeletionService.delete_map_file(farm_name, current_app.instance_path)
        except (OSError, ValueError):
            flash(f"{farm_name} deleted. The map file could not be removed.", "warning")
            return redirect(url_for("web.farms_page"))

        flash(f"{farm_name} deleted", "success")
        return redirect(url_for("web.farms_page"))

    @bp.get("/farms/<farm_id>")
    def farm_detail(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        paddocks = sorted(
            [paddock for paddock in farm.paddocks if paddock.status == "active"],
            key=lambda p: p.name.lower(),
        )
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
            located_mob_ids = {
                str(allocation.grazing_session.mob_id) for allocation in active_allocations
            }
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
        water_summary = WaterNetworkService.farm_summary(str(farm.id))
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
            water_summary=water_summary,
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
        override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
        try:
            name = PaddockService.validate_available_name(farm_id, request.form.get("name"))
        except ValueError as exc:
            flash(str(exc), "error")
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
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A paddock with this name already exists on this farm", "error")
            return redirect(url_for("web.farm_detail", farm_id=farm_id))
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

