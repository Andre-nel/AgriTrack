from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Farm, Mob, Paddock, RainfallRecord
from app.models.stock_ledger import StockEventType
from app.services.movement_service import MovementService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService

bp = Blueprint("web", __name__)


@bp.get("/")
def dashboard():
    summary = ReportingService.dashboard_summary()
    farms = Farm.query.order_by(Farm.name).all()
    return render_template("dashboard.html", summary=summary, farms=farms)


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
    mobs = sorted(farm.mobs, key=lambda m: m.name.lower())
    rainfall = (
        RainfallRecord.query.filter_by(farm_id=farm.id)
        .order_by(RainfallRecord.recorded_on.desc())
        .limit(20)
        .all()
    )
    return render_template("farm_detail.html", farm=farm, paddocks=paddocks, mobs=mobs, rainfall=rainfall)


@bp.post("/farms/<farm_id>/paddocks")
def create_paddock_form(farm_id):
    Farm.query.get_or_404(farm_id)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Paddock name is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    paddock = Paddock(
        farm_id=farm_id,
        name=name,
        area_ha=request.form.get("area_ha") or 0,
        grazeable_area_ha=request.form.get("grazeable_area_ha") or 0,
    )
    db.session.add(paddock)
    db.session.commit()
    flash("Paddock created", "success")
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
    db.session.commit()
    flash("Mob created", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.post("/farms/<farm_id>/rainfall")
def create_rainfall_form(farm_id):
    Farm.query.get_or_404(farm_id)
    recorded_on = request.form.get("recorded_on") or date.today().isoformat()
    mm = request.form.get("mm")
    if not mm:
        flash("Rainfall mm is required", "error")
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

    row = RainfallRecord(farm_id=farm_id, recorded_on=recorded_on, mm=mm, source="manual")
    db.session.add(row)
    db.session.commit()
    flash("Rainfall record added", "success")
    return redirect(url_for("web.farm_detail", farm_id=farm_id))


@bp.get("/mobs/<mob_id>")
def mob_detail(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    paddocks = Paddock.query.filter_by(farm_id=mob.farm_id).order_by(Paddock.name).all()
    return render_template("mob_detail.html", mob=mob, paddocks=paddocks, stock_event_types=StockEventType)


@bp.post("/mobs/<mob_id>/adjust")
def mob_adjust_form(mob_id):
    mob = Mob.query.get_or_404(mob_id)
    try:
        group_type = StockService.get_or_create_group_type(
            species=(request.form.get("species") or "cattle").strip(),
            breed=(request.form.get("breed") or "mixed").strip(),
            sex=(request.form.get("sex") or "mixed").strip(),
            age_class=(request.form.get("age_class") or "adult").strip(),
        )
        StockService.adjust_stock(
            mob_id=mob.id,
            farm_id=mob.farm_id,
            animal_group_type_id=group_type.id,
            event_type=StockEventType(request.form.get("event_type") or "adjustment_in"),
            quantity=int(request.form.get("quantity") or 0),
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
    mob = Mob.query.get_or_404(mob_id)
    paddock_id = request.form.get("paddock_id")
    if not paddock_id:
        flash("Paddock is required", "error")
        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    try:
        MovementService.move_mob(
            mob=mob,
            allocations=[{"paddock_id": paddock_id, "allocation_fraction": "1.0"}],
        )
        db.session.commit()
        flash("Mob moved", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("web.mob_detail", mob_id=mob_id))


@bp.get("/paddocks/<paddock_id>")
def paddock_detail(paddock_id):
    paddock = Paddock.query.get_or_404(paddock_id)
    stock = ReportingService.paddock_current_stock(paddock_id)
    history = sorted(
        paddock.grazing_allocations,
        key=lambda a: a.grazing_session.start_at,
        reverse=True,
    )
    return render_template("paddock_detail.html", paddock=paddock, stock=stock, history=history)
