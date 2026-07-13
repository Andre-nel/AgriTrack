from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    Shearer,
    ShearingBale,
    ShearingBaleCode,
    ShearingEntry,
    ShearingSession,
    ShearingSessionAttachment,
)
from app.services.shearing_attachment_service import ShearingAttachmentService
from app.services.shearing_service import ShearingService
from app.services.task_service import TaskService

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


def _session_group_types(session: ShearingSession):
    return [
        group_type
        for group_type in _sheep_goat_group_types()
        if group_type.species == session.species
    ]


def _shearers():
    return Shearer.query.order_by(Shearer.active.desc(), Shearer.name.asc()).all()


def _changed_form_value(name: str) -> tuple[str | None, bool]:
    if name not in request.form:
        return None, False
    value = request.form.get(name)
    original = request.form.get(f"original_{name}")
    return value, original is None or (value or "").strip() != (original or "").strip()


def _session_redirect(session_id: str, default: str = "detail"):
    target = (request.form.get("return_to") or request.args.get("return_to") or default).strip()
    endpoint_by_target = {
        "detail": "shearing.detail",
        "shearing": "shearing.shearing_page",
        "shearing_analytics": "shearing.shearing_analytics",
        "analytics": "shearing.session_analytics",
        "bales": "shearing.bales_page",
    }
    endpoint = endpoint_by_target.get(target, endpoint_by_target[default])
    return redirect(url_for(endpoint, session_id=session_id))


def _get_session_or_404(session_id: str) -> ShearingSession:
    return (
        ShearingSession.query.options(
            selectinload(ShearingSession.farm),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.shearer),
            selectinload(ShearingSession.entries).selectinload(ShearingEntry.animal_group_type),
            selectinload(ShearingSession.bales).selectinload(ShearingBale.bale_code),
            selectinload(ShearingSession.attachments),
        )
        .filter_by(id=session_id)
        .first_or_404()
    )


def _session_attachment_file_url(session: ShearingSession, attachment: ShearingSessionAttachment) -> str:
    return url_for(
        "shearing.session_attachment_file",
        session_id=session.id,
        attachment_id=attachment.id,
    )


def _session_attachment_rows(session: ShearingSession) -> list[dict]:
    tz_name = session.farm.timezone if session.farm else "SAST"
    rows = []
    for attachment in sorted(
        session.attachments,
        key=lambda item: (
            item.created_at.isoformat() if item.created_at else "",
            str(item.id),
        ),
        reverse=True,
    ):
        rows.append(
            {
                "id": str(attachment.id),
                "original_filename": attachment.original_filename,
                "content_type": attachment.content_type,
                "byte_size": attachment.byte_size,
                "caption": attachment.caption,
                "created_at": TaskService.format_local_datetime(attachment.created_at, tz_name),
                "is_image": (attachment.content_type or "").startswith("image/"),
                "url": _session_attachment_file_url(session, attachment),
            }
        )
    return rows


def _session_context(session: ShearingSession) -> dict:
    return {
        "session": session,
        "session_payload": ShearingService.serialize_session(session),
        "entries": ShearingService.sorted_entries(session.entries),
        "bales": ShearingService.sorted_bales(session.bales),
        "attachment_rows": _session_attachment_rows(session),
        "bale_codes": ShearingService.bale_codes_for_species(session.species),
        "shearers": _shearers(),
        "group_types": _session_group_types(session),
        "today": date.today().isoformat(),
        "format_money": _money,
        "serialize_entry": ShearingService.serialize_entry,
        "serialize_bale": ShearingService.serialize_bale,
    }


def _batch_row_indexes() -> list[int]:
    indexes: set[int] = set()
    try:
        row_count = int(request.form.get("row_count") or 0)
    except ValueError:
        row_count = 0
    indexes.update(range(max(row_count, 0)))
    for key in request.form:
        if not key.startswith("rows-"):
            continue
        parts = key.split("-", 2)
        if len(parts) < 3:
            continue
        try:
            indexes.add(int(parts[1]))
        except ValueError:
            continue
    return sorted(indexes)


def _batch_row_value(index: int, name: str) -> str:
    return (request.form.get(f"rows-{index}-{name}") or "").strip()


def _batch_row_is_empty(row: dict) -> bool:
    return not any(
        str(row.get(name) or "").strip()
        for name in (
            "shearer_id",
            "animal_group_type_id",
            "quantity",
            "note",
            "breed",
            "sex",
            "age_class",
        )
    )


def _record_batch_entries(session: ShearingSession) -> int:
    work_date = request.form.get("work_date")
    date_value = ShearingService.parse_date(work_date, "work_date")
    ShearingService.validate_work_date(session, date_value)

    rows = []
    for index in _batch_row_indexes():
        row = {
            "row_number": index + 1,
            "shearer_id": _batch_row_value(index, "shearer_id"),
            "animal_group_type_id": _batch_row_value(index, "animal_group_type_id"),
            "quantity": _batch_row_value(index, "quantity"),
            "note": _batch_row_value(index, "note"),
            "use_new_type": _batch_row_value(index, "use_new_type") == "1",
            "breed": _batch_row_value(index, "breed"),
            "sex": _batch_row_value(index, "sex"),
            "age_class": _batch_row_value(index, "age_class"),
        }
        if _batch_row_is_empty(row):
            continue

        try:
            group_type = ShearingService.group_type_from_payload(
                {
                    "animal_group_type_id": "" if row["use_new_type"] else row["animal_group_type_id"],
                    "animal_group_type": {
                        "species": session.species,
                        "breed": row["breed"],
                        "sex": row["sex"],
                        "age_class": row["age_class"],
                    },
                },
                expected_species=session.species,
            )
        except ValueError as exc:
            raise ValueError(f"Row {row['row_number']}: {exc}") from exc
        row["animal_group_type"] = group_type
        rows.append(row)

    if not rows:
        raise ValueError("At least one daily count row is required")

    seen_keys: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["shearer_id"], str(row["animal_group_type"].id))
        previous_row = seen_keys.get(key)
        if previous_row is not None:
            raise ValueError(
                "Rows "
                f"{previous_row} and {row['row_number']} duplicate the same shearer and animal type for this date"
            )
        seen_keys[key] = row["row_number"]

    for row in rows:
        try:
            ShearingService.record_entry(
                session=session,
                work_date=date_value,
                shearer_id=row["shearer_id"],
                animal_group_type=row["animal_group_type"],
                quantity=row["quantity"],
                note=row["note"],
            )
        except ValueError as exc:
            raise ValueError(f"Row {row['row_number']}: {exc}") from exc
    return len(rows)


def _selected_shearing_analytics_group_by() -> list[str]:
    valid = {"date", "shearer", "animal_type"}
    requested = [value for value in request.args.getlist("group_by") if value in valid]
    return requested or ["date", "shearer"]


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
    return render_template(
        "shearing/detail.html",
        **_session_context(session),
    )


@bp.get("/sessions/<session_id>/shearing")
def shearing_page(session_id: str):
    session = _get_session_or_404(session_id)
    return render_template(
        "shearing/shearing.html",
        daily_breakdown=ShearingService.daily_breakdown(session),
        **_session_context(session),
    )


@bp.get("/sessions/<session_id>/shearing/analytics")
def shearing_analytics(session_id: str):
    return _render_session_analytics(session_id)


@bp.get("/sessions/<session_id>/analytics")
def session_analytics(session_id: str):
    return _render_session_analytics(session_id)


def _render_session_analytics(session_id: str):
    session = _get_session_or_404(session_id)
    selected_group_by = _selected_shearing_analytics_group_by()
    return render_template(
        "shearing/shearing_analytics.html",
        chart_payload=ShearingService.shearing_analytics_chart_payload(
            session,
            group_by=selected_group_by,
        ),
        bale_chart_payload=ShearingService.bale_chart_payload(session),
        group_by_options=[
            {"value": "date", "label": "Date"},
            {"value": "shearer", "label": "Shearer"},
            {"value": "animal_type", "label": "Animal Type"},
        ],
        selected_group_by=selected_group_by,
        **_session_context(session),
    )


@bp.get("/sessions/<session_id>/bales")
def bales_page(session_id: str):
    session = _get_session_or_404(session_id)
    return render_template(
        "shearing/bales.html",
        bale_statistics=ShearingService.bale_statistics(session),
        **_session_context(session),
    )


@bp.post("/sessions/<session_id>/attachments")
def upload_session_attachments(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        uploads = request.files.getlist("attachments")
        if not any(upload is not None and upload.filename for upload in uploads):
            raise ValueError("Choose at least one attachment to upload")
        attachments = ShearingAttachmentService.create_attachments_from_uploads(
            uploads,
            session=session,
            instance_path=current_app.instance_path,
            uploaded_by_user_id=(str(g.web_user.id) if getattr(g, "web_user", None) is not None else None),
            caption=request.form.get("caption"),
        )
        db.session.commit()
        flash(f"{len(attachments)} attachment{'s' if len(attachments) != 1 else ''} uploaded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _session_redirect(session_id)


@bp.get("/sessions/<session_id>/attachments/<attachment_id>")
def session_attachment_file(session_id: str, attachment_id: str):
    attachment = ShearingSessionAttachment.query.filter_by(
        id=attachment_id,
        session_id=session_id,
    ).first_or_404()
    try:
        target = ShearingAttachmentService.absolute_attachment_path(
            current_app.instance_path,
            attachment.storage_path,
        )
    except FileNotFoundError:
        abort(404)
    if not target.exists():
        abort(404)
    return send_file(
        target,
        mimetype=attachment.content_type,
        as_attachment=False,
        download_name=attachment.original_filename,
    )


@bp.post("/sessions/<session_id>/attachments/<attachment_id>/delete")
def delete_session_attachment(session_id: str, attachment_id: str):
    attachment = ShearingSessionAttachment.query.filter_by(
        id=attachment_id,
        session_id=session_id,
    ).first_or_404()
    try:
        ShearingAttachmentService.delete_attachment(
            attachment,
            instance_path=current_app.instance_path,
        )
        db.session.commit()
        flash("Attachment deleted", "success")
    except FileNotFoundError:
        db.session.rollback()
        abort(404)
    return _session_redirect(session_id)


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
    return _session_redirect(session_id)


@bp.post("/sessions/<session_id>/close")
def close_session(session_id: str):
    session = _get_session_or_404(session_id)
    ShearingService.close_session(session)
    db.session.commit()
    flash("Shearing session closed", "success")
    return _session_redirect(session_id)


@bp.post("/sessions/<session_id>/reopen")
def reopen_session(session_id: str):
    session = _get_session_or_404(session_id)
    ShearingService.reopen_session(session)
    db.session.commit()
    flash("Shearing session reopened", "success")
    return _session_redirect(session_id)


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
        return _session_redirect(return_session_id)
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


@bp.post("/sessions/<session_id>/entries/batch")
def record_entries_batch(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        row_count = _record_batch_entries(session)
        db.session.commit()
        flash(f"{row_count} shearing count row{'s' if row_count != 1 else ''} saved", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _session_redirect(session_id, "shearing")


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
    return _session_redirect(session_id, "shearing")


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
    return _session_redirect(session_id, "bales")


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
    return _session_redirect(session_id, "bales")


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
    return _session_redirect(session_id, "shearing")
