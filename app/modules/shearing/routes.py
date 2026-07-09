from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AnimalGroupType, Farm, Shearer, ShearingBale, ShearingBaleCode, ShearingEntry, ShearingSession
from app.services.shearing_service import ShearingService

bp = Blueprint("shearing", __name__, url_prefix="/shearing")


def _money(value) -> str:
    return f"R {float(value or 0):,.2f}"


def _farm_options():
    return Farm.query.filter_by(active=True).order_by(Farm.name.asc()).all()


def _sheep_goat_group_types():
    return (
        AnimalGroupType.query.filter(AnimalGroupType.species.in_(("Sheep", "Goat")))
        .order_by(
            AnimalGroupType.species.asc(),
            AnimalGroupType.breed.asc(),
            AnimalGroupType.sex.asc(),
            AnimalGroupType.age_class.asc(),
        )
        .all()
    )


def _changed_form_value(name: str) -> tuple[str | None, bool]:
    if name not in request.form:
        return None, False
    value = request.form.get(name)
    original = request.form.get(f"original_{name}")
    return value, original is None or (value or "").strip() != (original or "").strip()


def _get_session_or_404(session_id: str) -> ShearingSession:
    return (
        ShearingSession.query.options(
            selectinload(ShearingSession.farm),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.shearer),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.animal_group_type),
            selectinload(ShearingSession.bales).selectinload(ShearingBale.bale_code),
        )
        .filter_by(id=session_id)
        .first_or_404()
    )


@bp.get("/")
def index():
    farms = _farm_options()
    selected_farm_id = (request.args.get("farm_id") or "").strip()
    if not selected_farm_id and farms:
        selected_farm_id = str(farms[0].id)

    sessions = []
    shearers = []
    bale_codes = ShearingService.bale_codes_for_species()
    if selected_farm_id:
        sessions = ShearingService.sessions_for_farm(selected_farm_id)
        shearers = (
            Shearer.query.order_by(Shearer.active.desc(), Shearer.name.asc())
            .all()
        )

    return render_template(
        "shearing/index.html",
        farms=farms,
        selected_farm_id=selected_farm_id,
        sessions=sessions,
        shearers=shearers,
        bale_codes=bale_codes,
        today=date.today().isoformat(),
        format_money=_money,
        serialize_session=ShearingService.serialize_session,
    )


@bp.post("/sessions")
def create_session():
    farm_id = (request.form.get("farm_id") or "").strip()
    try:
        session = ShearingService.create_session(
            farm_id=farm_id,
            name=request.form.get("name"),
            species=request.form.get("species"),
            start_date=request.form.get("start_date"),
            end_date=request.form.get("end_date"),
            lootjie_rate=request.form.get("lootjie_rate"),
            notes=request.form.get("notes"),
        )
        db.session.commit()
        flash("Shearing session created", "success")
        return redirect(url_for("shearing.detail", session_id=session.id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("shearing.index", farm_id=farm_id))


@bp.get("/sessions/<session_id>")
def detail(session_id: str):
    session = _get_session_or_404(session_id)
    shearers = Shearer.query.order_by(Shearer.active.desc(), Shearer.name.asc()).all()
    group_types = [
        group_type
        for group_type in _sheep_goat_group_types()
        if group_type.species == session.species
    ]
    bale_codes = ShearingService.bale_codes_for_species(session.species)
    session_payload = ShearingService.serialize_session(session)
    return render_template(
        "shearing/detail.html",
        session=session,
        session_payload=session_payload,
        entries=ShearingService.sorted_entries(session.entries),
        bales=ShearingService.sorted_bales(session.bales),
        bale_codes=bale_codes,
        shearers=shearers,
        group_types=group_types,
        today=date.today().isoformat(),
        format_money=_money,
        serialize_entry=ShearingService.serialize_entry,
        serialize_bale=ShearingService.serialize_bale,
    )


@bp.post("/sessions/<session_id>/edit")
def edit_session(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        ShearingService.update_session(
            session,
            name=request.form.get("name"),
            species=request.form.get("species"),
            start_date=request.form.get("start_date"),
            end_date=request.form.get("end_date"),
            lootjie_rate=request.form.get("lootjie_rate"),
            notes=request.form.get("notes"),
            status=request.form.get("status") or session.status,
        )
        db.session.commit()
        flash("Shearing session updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/sessions/<session_id>/close")
def close_session(session_id: str):
    session = _get_session_or_404(session_id)
    ShearingService.close_session(session)
    db.session.commit()
    flash("Shearing session closed", "success")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/sessions/<session_id>/reopen")
def reopen_session(session_id: str):
    session = _get_session_or_404(session_id)
    ShearingService.reopen_session(session)
    db.session.commit()
    flash("Shearing session reopened", "success")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/shearers")
def create_shearer():
    farm_id = (request.form.get("farm_id") or "").strip()
    return_session_id = (request.form.get("return_session_id") or "").strip()
    try:
        ShearingService.create_shearer(farm_id=farm_id, name=request.form.get("name"))
        db.session.commit()
        flash("Shearer saved", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    if return_session_id:
        return redirect(url_for("shearing.detail", session_id=return_session_id))
    return redirect(url_for("shearing.index", farm_id=farm_id))


@bp.post("/shearers/<shearer_id>/toggle")
def toggle_shearer(shearer_id: str):
    shearer = Shearer.query.filter_by(id=shearer_id).first_or_404()
    farm_id = (request.form.get("farm_id") or request.args.get("farm_id") or "").strip()
    ShearingService.set_shearer_active(shearer, not shearer.active)
    db.session.commit()
    flash("Shearer updated", "success")
    return redirect(url_for("shearing.index", farm_id=farm_id))


@bp.post("/bale-codes")
def create_bale_code():
    farm_id = (request.form.get("farm_id") or "").strip()
    try:
        ShearingService.upsert_bale_code(
            species=request.form.get("species"),
            code=request.form.get("code"),
            line_type=request.form.get("line_type"),
            age_group=request.form.get("age_group"),
            fineness_grade=request.form.get("fineness_grade"),
            length_code=request.form.get("length_code"),
            fineness_micron=request.form.get("fineness_micron"),
            clean_yield_percent=request.form.get("clean_yield_percent"),
            color=request.form.get("color"),
            vegetable_matter=request.form.get("vegetable_matter"),
            style_character=request.form.get("style_character"),
            consistency=request.form.get("consistency"),
            fault=request.form.get("fault"),
            description=request.form.get("description"),
            notes=request.form.get("notes"),
        )
        db.session.commit()
        flash("Bale code saved", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.index", farm_id=farm_id))


@bp.post("/bale-codes/<bale_code_id>/toggle")
def toggle_bale_code(bale_code_id: str):
    bale_code = ShearingBaleCode.query.filter_by(id=bale_code_id).first_or_404()
    farm_id = (request.form.get("farm_id") or request.args.get("farm_id") or "").strip()
    ShearingService.set_bale_code_active(bale_code, not bale_code.active)
    db.session.commit()
    flash("Bale code updated", "success")
    return redirect(url_for("shearing.index", farm_id=farm_id))


@bp.post("/sessions/<session_id>/entries")
def record_entry(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        group_type = ShearingService.group_type_from_payload(
            {
                "animal_group_type_id": request.form.get("animal_group_type_id"),
                "animal_group_type": {
                    "species": session.species,
                    "breed": request.form.get("breed"),
                    "sex": request.form.get("sex"),
                    "age_class": request.form.get("age_class"),
                },
            },
            expected_species=session.species,
        )
        entry_kwargs = {
            "session": session,
            "work_date": request.form.get("work_date"),
            "shearer_id": request.form.get("shearer_id"),
            "animal_group_type": group_type,
            "quantity": request.form.get("quantity"),
            "note": request.form.get("note"),
            "entry_id": request.form.get("entry_id"),
        }
        unit_rate, unit_rate_changed = _changed_form_value("unit_rate")
        if unit_rate_changed:
            entry_kwargs["unit_rate"] = unit_rate
        line_amount, line_amount_changed = _changed_form_value("line_amount")
        if line_amount_changed:
            entry_kwargs["line_amount"] = line_amount

        entry = ShearingService.record_entry(**entry_kwargs)
        db.session.commit()
        flash("Shearing count saved" if entry is not None else "Shearing count removed", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/sessions/<session_id>/bales")
def record_bale(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        bale_code = None
        bale_code_id = (request.form.get("bale_code_id") or "").strip()
        if bale_code_id:
            bale_code = ShearingService.bale_code_from_payload(
                {"bale_code_id": bale_code_id},
                expected_species=session.species,
            )
        bale = ShearingService.record_bale(
            session=session,
            bale_id=request.form.get("bale_id"),
            bale_code=bale_code,
            code_text=request.form.get("code_text"),
            bale_number=request.form.get("bale_number"),
            weight_kg=request.form.get("weight_kg"),
            price_per_kg=request.form.get("price_per_kg"),
            total_price=request.form.get("total_price"),
            notes=request.form.get("notes"),
        )
        db.session.commit()
        flash("Bale saved" if bale is not None else "Bale updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/sessions/<session_id>/bales/<bale_id>/delete")
def delete_bale(session_id: str, bale_id: str):
    session = _get_session_or_404(session_id)
    try:
        ShearingService.delete_bale(session=session, bale_id=bale_id)
        db.session.commit()
        flash("Bale deleted", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.detail", session_id=session_id))


@bp.post("/sessions/<session_id>/entries/<entry_id>/delete")
def delete_entry(session_id: str, entry_id: str):
    session = _get_session_or_404(session_id)
    try:
        ShearingService.delete_entry(session=session, entry_id=entry_id)
        db.session.commit()
        flash("Shearing count deleted", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("shearing.detail", session_id=session_id))
