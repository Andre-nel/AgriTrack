from datetime import date
from uuid import uuid4

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, Farm, LivestockTrade
from app.services.cash_flow_service import CashFlowService
from app.services.finance_service import FinanceService
from app.services.livestock_trade_service import LivestockTradeService
from app.services.livestock_trade_attachment_service import LivestockTradeAttachmentService
from app.services.livestock_trade_calculations import payload_statistics
from app.services.livestock_trade_payload import (
    MAX_DOCUMENT_BYTES, check_shape, document_from_form, load_document,
    normalize_document, public_sex, template_document,
)
from app.services.stock_service import StockService

bp = Blueprint("finance", __name__)
FORM_MINIMUM_LINE_COUNT = 6


@bp.before_request
def _trade_form_limits():
    # Large weight lists also travel through ordinary form fields during review.
    # Keep this allowance scoped to trade routes, including manual edits.
    if request.path.startswith("/cash-flow/livestock-trades"):
        request.max_form_memory_size = MAX_DOCUMENT_BYTES
        image_limit = LivestockTradeAttachmentService.MAX_ATTACHMENT_BYTES * LivestockTradeAttachmentService.MAX_ATTACHMENTS
        request.max_content_length = image_limit + MAX_DOCUMENT_BYTES * 2


def _review_identity(payload, images=()):
    identity = [payload["import_id"], LivestockTradeService.payload_hash(payload)]
    if images:
        identity.append(LivestockTradeAttachmentService.fingerprints(images))
    return identity


def _import_image_owner():
    if "livestock_trade_upload_owner" not in session:
        session["livestock_trade_upload_owner"] = str(uuid4())
    return session["livestock_trade_upload_owner"]


def _retained_import_images():
    return LivestockTradeAttachmentService.resolve_tokens(
        request.form.getlist("attachment_tokens"), _import_image_owner(),
    )


def _update_import_images(existing):
    owner = _import_image_owner()
    removed = set(request.form.getlist("remove_attachment_tokens"))
    retained = [image for image in existing if image["token"] not in removed]
    images = LivestockTradeAttachmentService.stage_uploads(
        request.files.getlist("trade_images"), owner, existing=retained,
    )
    # Delete removed images only after every newly selected image has been accepted.
    LivestockTradeAttachmentService.discard_tokens(
        [image["token"] for image in existing if image["token"] in removed], owner,
    )
    return images


def _cash_flow_redirect(*, year: int, tab: str):
    return redirect(url_for("finance.cash_flow", year=year, tab=tab))


def _build_form_line_rows(lines=None, *, minimum_rows: int = FORM_MINIMUM_LINE_COUNT) -> list[dict]:
    rows = []
    for line in lines or []:
        rows.append(
            {
                "species_scope": line.species_scope or "",
                "category_code": line.category_code,
                "amount": f"{line.amount:.2f}",
            }
        )

    while len(rows) < minimum_rows:
        rows.append({"species_scope": "", "category_code": "", "amount": ""})
    return rows


def _build_transaction_rows(year: int) -> list[dict]:
    start_date = date(year, 1, 1)
    end_date = date(year + 1, 1, 1)
    transactions = (
        CashTransaction.query.options(
            selectinload(CashTransaction.farm),
            selectinload(CashTransaction.lines),
        )
        .filter(CashTransaction.transaction_date >= start_date)
        .filter(CashTransaction.transaction_date < end_date)
        .order_by(CashTransaction.transaction_date.desc(), CashTransaction.created_at.desc())
        .all()
    )
    managed_trade_map = LivestockTradeService.managed_trade_map(
        [str(transaction.id) for transaction in transactions]
    )

    rows = []
    for transaction in transactions:
        managed_trade = managed_trade_map.get(str(transaction.id))
        lines = sorted(
            list(transaction.lines),
            key=FinanceService.transaction_line_sort_key,
        )
        rows.append(
            {
                "id": transaction.id,
                "transaction_date": transaction.transaction_date.isoformat(),
                "farm_id": transaction.farm_id or "",
                "farm_name": transaction.farm.name if transaction.farm else "Business-wide",
                "reference": transaction.reference,
                "counterparty": transaction.counterparty,
                "description": transaction.description,
                "managed_trade_id": str(managed_trade.id) if managed_trade else "",
                "line_rows": _build_form_line_rows(lines, minimum_rows=max(FORM_MINIMUM_LINE_COUNT, len(lines) + 1)),
                "lines": [
                    {
                        "species_scope": FinanceService.species_label(line.species_scope),
                        "category_label": FinanceService.category_label(line.category_code),
                        "direction_label": FinanceService.direction_label(line.direction),
                        "amount": line.amount,
                    }
                    for line in lines
                ],
            }
        )
    return rows


def _get_livestock_trade_or_404(trade_id: str) -> LivestockTrade:
    return (
        LivestockTradeService.trade_query()
        .filter(LivestockTrade.id == trade_id)
        .first_or_404()
    )


def _animal_group_type_options() -> list[AnimalGroupType]:
    return (
        AnimalGroupType.query.order_by(
            AnimalGroupType.species.asc(),
            AnimalGroupType.breed.asc(),
            AnimalGroupType.sex.asc(),
            AnimalGroupType.age_class.asc(),
        )
        .all()
    )


def _format_rate(value) -> str:
    if value is None:
        return "N/A"
    text = f"{float(value):,.4f}".replace(",", " ").replace(".", ",")
    return f"R{text}"


def _format_number(value, decimals: int = 3) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{decimals}f}"


def _input_decimal(value, decimals: int = 2) -> str:
    if value is None:
        return ""
    return f"{float(value):.{decimals}f}"


def _parse_report_dates():
    today = date.today()
    try:
        start_date = LivestockTradeService.optional_date(request.args.get("start_date"), "Start date") or date(
            today.year,
            1,
            1,
        )
        end_date = LivestockTradeService.optional_date(request.args.get("end_date"), "End date") or today
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if end_date < start_date:
        raise ValueError("End date must be on or after the start date")
    return start_date, end_date


def _livestock_trade_context():
    farms = Farm.query.filter_by(active=True).order_by(Farm.name.asc()).all()
    return {
        "farms": farms,
        "animal_group_types": _animal_group_type_options(),
        "sex_options": sorted({value for values in StockService.SEX_OPTIONS.values() for value in values}),
        "age_class_options": sorted({value for values in StockService.AGE_CLASS_OPTIONS.values() for value in values}),
        "format_currency": FinanceService.format_currency,
        "format_rate": _format_rate,
        "format_number": _format_number,
        "input_decimal": _input_decimal,
        "public_sex": public_sex,
    }


def _preview_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="livestock-trade-preview-v1")


def _import_source():
    if "group_count" in request.form:
        document = document_from_form(request.form)
        check_shape(document)
        return document
    upload = request.files.get("trade_file")
    raw = request.form.get("trade_json", "")
    if upload and upload.filename:
        if raw.strip():
            raise ValueError("Use either a file or pasted JSON, not both")
        content = upload.read(MAX_DOCUMENT_BYTES + 1)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError("The JSON document must be 2 MiB or smaller")
        try:
            raw = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Upload a UTF-8 JSON file") from exc
    return load_document(raw)


def _render_import(document=None, *, images=(), error=None, status=200, allow_confirmation=True):
    selected = request.form.getlist("farm_ids")
    preview, rows, token = None, [], None
    if document is not None:
        try:
            payload = normalize_document(document, selected, require_details=False)
            document["species"] = payload["species"]
            preview = payload_statistics(payload)
            rows = [(group["label"] or f"Group {index}", stats) for index, (group, stats) in enumerate(zip(payload["groups"], preview["groups"]), 1)]
            rows.append(("Combined trade", preview["stats"]))
            complete = normalize_document(document, selected)
            if allow_confirmation:
                token = _preview_signer().dumps(_review_identity(complete, images))
        except ValueError as exc:
            error = error or str(exc)
        # Display legacy taxonomy codes as human-readable values without guessing.
        if document.get("species") in ("Cattle", "Sheep", "Goat"):
            for group in document["groups"]:
                if isinstance(group.get("sex"), str):
                    group["sex"] = public_sex(document["species"], group["sex"])
    return render_template(
        "finance/livestock_trade_import.html", trade_document=document, selected_farm_ids=selected,
        error=error, preview=preview, preview_rows=rows, preview_token=token,
        import_images=images,
        raw_json=request.form.get("trade_json", ""), **_livestock_trade_context(),
    ), status


@bp.get("/cash-flow/livestock-trades/import")
def import_livestock_trade():
    return _render_import()


@bp.get("/cash-flow/livestock-trades/import-id")
def generate_livestock_trade_import_id():
    response = jsonify(import_id=str(uuid4()))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/cash-flow/livestock-trades/import/images")
def preview_livestock_trade_image():
    try:
        image = LivestockTradeAttachmentService.describe_token(
            request.args.get("token", ""), _import_image_owner(),
        )
    except ValueError:
        abort(404)
    return _send_trade_image(LivestockTradeAttachmentService.staged_path(image), image)


def _send_trade_image(path, image):
    if not path.is_file():
        abort(404)
    response = send_file(
        path, mimetype=image["content_type"], download_name=image["original_filename"],
        as_attachment=image["content_type"] in ("image/heic", "image/heif"),
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/cash-flow/livestock-trades/import/discard")
def discard_livestock_trade_import():
    try:
        LivestockTradeAttachmentService.discard_tokens(
            request.form.getlist("attachment_tokens"), _import_image_owner(),
        )
    except ValueError:
        # Never delete another session's images even when a submitted token is invalid.
        flash("The attachment reference was invalid; no referenced images were removed.", "error")
    flash("Unsaved trade discarded", "success")
    return redirect(url_for("finance.import_livestock_trade"))


@bp.get("/cash-flow/livestock-trades/template")
def livestock_trade_template():
    response = jsonify(template_document())
    response.headers["Content-Disposition"] = 'attachment; filename="livestock-trade.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/cash-flow/livestock-trades/import-instructions")
def livestock_trade_instructions():
    return send_file(current_app.root_path + "/modules/finance/import_instructions.txt", as_attachment=True, download_name="livestock-trade-chatgpt-instructions.txt")


@bp.post("/cash-flow/livestock-trades/import/preview")
def preview_livestock_trade_import():
    document, images = None, []
    try:
        images = _retained_import_images()
        images = _update_import_images(images)
        document = _import_source()
    except ValueError as exc:
        if document is None:
            try:
                document = _import_source()
            except ValueError:
                pass
        return _render_import(document, images=images, error=str(exc), status=400, allow_confirmation=False)
    return _render_import(document, images=images)


@bp.post("/cash-flow/livestock-trades/import/confirm")
def confirm_livestock_trade_import():
    document, images = None, []
    images_valid = False
    try:
        images = _retained_import_images()
        images = _update_import_images(images)
        images_valid = True
        document = _import_source()
        payload = normalize_document(document, request.form.getlist("farm_ids"))
        try:
            reviewed_hash = _preview_signer().loads(request.form.get("preview_token", ""), max_age=3600)
        except BadSignature as exc:
            raise ValueError("Preview the completed trade before saving (previews expire after one hour)") from exc
        if reviewed_hash != _review_identity(payload, images):
            raise ValueError("The details changed. Review the updated preview below, then save again.")
        trade, created = LivestockTradeService.import_document(
            document, request.form.getlist("farm_ids"), attachments=images,
        )
        flash("Livestock trade recorded" if created else "This import is already recorded; no duplicate was created", "success")
        return redirect(url_for("finance.livestock_trade_detail", trade_id=trade.id))
    except ValueError as exc:
        db.session.rollback()
        if document is None:
            try:
                document = _import_source()
            except ValueError:
                pass
        return _render_import(document, images=images, error=str(exc), status=400, allow_confirmation=images_valid)


@bp.get("/cash-flow")
def cash_flow():
    try:
        selected_year = FinanceService.parse_year(request.args.get("year"))
    except ValueError:
        flash("Year must be valid", "error")
        return redirect(url_for("finance.cash_flow"))

    selected_tab = CashFlowService.normalize_tab(request.args.get("tab"))
    dashboard = CashFlowService.build_dashboard(selected_year)
    livestock_context = _livestock_trade_context()

    return render_template(
        "finance/cash_flow.html",
        selected_year=selected_year,
        selected_tab=selected_tab,
        years=FinanceService.available_years(selected_year),
        dashboard=dashboard,
        active_statement=dashboard["statements"][selected_tab],
        category_groups=FinanceService.category_select_groups(),
        species_scope_options=FinanceService.species_scope_options(),
        new_line_rows=_build_form_line_rows(),
        recent_transactions=_build_transaction_rows(selected_year),
        recent_livestock_trades=list(
            reversed(
                [
                    LivestockTradeService.serialize_trade(trade)
                    for trade in LivestockTradeService.trades_for_period(
                        start_date=date(selected_year, 1, 1),
                        end_date=date(selected_year, 12, 31),
                    )
                ]
            )
        )[:8],
        **livestock_context,
    )


@bp.get("/cash-flow/livestock-trades")
def livestock_trades():
    try:
        start_date, end_date = _parse_report_dates()
        selected_trade_type = (request.args.get("trade_type") or "").strip().lower()
        selected_species = (request.args.get("species") or "").strip()
        selected_farm_id = (request.args.get("farm_id") or "").strip()
        report = LivestockTradeService.build_period_report(
            start_date=start_date,
            end_date=end_date,
            trade_type=selected_trade_type or None,
            species=selected_species or None,
            farm_id=selected_farm_id or None,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("finance.livestock_trades"))

    return render_template(
        "finance/livestock_trades.html",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        selected_trade_type=selected_trade_type,
        selected_species=selected_species,
        selected_farm_id=selected_farm_id,
        report=report,
        chart_payload=report["chart_payload"],
        species_options=["Sheep", "Cattle", "Goat"],
        **_livestock_trade_context(),
    )


@bp.get("/cash-flow/livestock-trades/new")
def new_livestock_trade():
    try:
        return_year = FinanceService.parse_year(
            request.args.get("return_year"),
            default=date.today().year,
        )
    except ValueError:
        return_year = date.today().year
    return_tab = CashFlowService.normalize_tab(request.args.get("return_tab"))

    return render_template(
        "finance/livestock_trade_new.html",
        return_year=return_year,
        return_tab=return_tab,
        default_trade_date=date.today().isoformat(),
        trade_document={**template_document(), "import_id": None, "date": date.today().isoformat()},
        selected_farm_ids=[],
        **_livestock_trade_context(),
    )


@bp.post("/cash-flow/livestock-trades")
def create_livestock_trade():
    try:
        trade = LivestockTradeService.create_trade_from_form(request.form)
        db.session.commit()
        flash("Livestock trade recorded", "success")
        return redirect(url_for("finance.livestock_trade_detail", trade_id=trade.id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")

        if "group_count" in request.form:
            try:
                document = document_from_form(request.form)
                return render_template("finance/livestock_trade_new.html", trade_document=document,
                    selected_farm_ids=request.form.getlist("farm_ids"), return_year=date.today().year,
                    return_tab="overall", default_trade_date="", **_livestock_trade_context()), 400
            except ValueError:
                pass

    try:
        year = FinanceService.parse_year(request.form.get("return_year"), default=date.today().year)
    except ValueError:
        year = date.today().year
    tab = CashFlowService.normalize_tab(request.form.get("return_tab"))
    if (request.form.get("return_destination") or "").strip() == "new":
        return redirect(url_for("finance.new_livestock_trade", return_year=year, return_tab=tab))
    return _cash_flow_redirect(year=year, tab=tab)


@bp.get("/cash-flow/livestock-trades/<trade_id>")
def livestock_trade_detail(trade_id: str):
    trade = _get_livestock_trade_or_404(trade_id)
    return render_template(
        "finance/livestock_trade_detail.html",
        trade=LivestockTradeService.serialize_trade(trade),
        trade_model=trade,
        trade_document=LivestockTradeService.document_for_trade(trade),
        selected_farm_ids=[link.farm_id for link in trade.farm_links],
        **_livestock_trade_context(),
    )


@bp.post("/cash-flow/livestock-trades/<trade_id>/edit")
def edit_livestock_trade(trade_id: str):
    trade = _get_livestock_trade_or_404(trade_id)
    try:
        LivestockTradeService.update_trade_from_form(trade, request.form)
        db.session.commit()
        flash("Livestock trade updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        if "group_count" in request.form:
            try:
                document = document_from_form(request.form)
                return render_template("finance/livestock_trade_detail.html", trade=LivestockTradeService.serialize_trade(trade),
                    trade_model=trade, trade_document=document, selected_farm_ids=request.form.getlist("farm_ids"),
                    edit_error=True, **_livestock_trade_context()), 400
            except ValueError:
                pass
    return redirect(url_for("finance.livestock_trade_detail", trade_id=trade_id))


@bp.get("/cash-flow/livestock-trades/<trade_id>/images/<attachment_id>")
def livestock_trade_image(trade_id: str, attachment_id: str):
    trade = _get_livestock_trade_or_404(trade_id)
    attachment = next((image for image in trade.attachments if image.id == attachment_id), None)
    if attachment is None:
        abort(404)
    return _send_trade_image(
        LivestockTradeAttachmentService.permanent_path(attachment),
        {"content_type": attachment.content_type, "original_filename": attachment.original_filename},
    )


@bp.post("/cash-flow/livestock-trades/<trade_id>/delete")
def delete_livestock_trade(trade_id: str):
    trade = _get_livestock_trade_or_404(trade_id)
    image_paths = [image.storage_path for image in trade.attachments]
    LivestockTradeService.delete_trade(trade)
    db.session.commit()
    LivestockTradeAttachmentService.delete_files(image_paths)
    flash("Livestock trade deleted", "success")
    return redirect(url_for("finance.livestock_trades"))


@bp.post("/cash-flow/transactions")
def create_transaction():
    tab = CashFlowService.normalize_tab(request.form.get("return_tab"))
    try:
        year = FinanceService.parse_year(request.form.get("return_year"), default=date.today().year)
    except ValueError:
        year = date.today().year

    try:
        FinanceService.create_transaction_from_form(request.form)
        db.session.commit()
        flash("Cash transaction recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _cash_flow_redirect(year=year, tab=tab)


@bp.post("/cash-flow/transactions/<transaction_id>/edit")
def edit_transaction(transaction_id: str):
    tab = CashFlowService.normalize_tab(request.form.get("return_tab"))
    try:
        year = FinanceService.parse_year(request.form.get("return_year"), default=date.today().year)
    except ValueError:
        year = date.today().year

    transaction = (
        CashTransaction.query.options(selectinload(CashTransaction.lines))
        .filter_by(id=transaction_id)
        .first_or_404()
    )
    managed_trade = LivestockTradeService.managed_trade_for_transaction(transaction)
    if managed_trade is not None:
        flash("Livestock trade cash transactions are managed from the livestock trade detail page.", "error")
        return redirect(url_for("finance.livestock_trade_detail", trade_id=managed_trade.id))
    try:
        FinanceService.update_transaction_from_form(transaction, request.form)
        db.session.commit()
        flash("Cash transaction updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _cash_flow_redirect(year=year, tab=tab)


@bp.post("/cash-flow/transactions/<transaction_id>/delete")
def delete_transaction(transaction_id: str):
    transaction = CashTransaction.query.filter_by(id=transaction_id).first_or_404()
    managed_trade = LivestockTradeService.managed_trade_for_transaction(transaction)
    if managed_trade is not None:
        flash("Livestock trade cash transactions are managed from the livestock trade detail page.", "error")
        return redirect(url_for("finance.livestock_trade_detail", trade_id=managed_trade.id))
    try:
        year = FinanceService.parse_year(request.form.get("return_year"), default=date.today().year)
    except ValueError:
        year = date.today().year
    tab = CashFlowService.normalize_tab(request.form.get("return_tab"))
    FinanceService.delete_transaction(transaction)
    db.session.commit()
    flash("Cash transaction deleted", "success")
    return _cash_flow_redirect(year=year, tab=tab)
