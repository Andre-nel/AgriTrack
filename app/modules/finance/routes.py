from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, Farm, LivestockTrade
from app.services.cash_flow_service import CashFlowService
from app.services.finance_service import FinanceService
from app.services.livestock_trade_service import LivestockTradeService
from app.services.stock_service import StockService

bp = Blueprint("finance", __name__)
FORM_MINIMUM_LINE_COUNT = 6


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
        return "-"
    text = f"{float(value):,.4f}".replace(",", " ").replace(".", ",")
    return f"R{text}"


def _format_number(value, decimals: int = 3) -> str:
    if value is None:
        return "-"
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
    }


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
    return redirect(url_for("finance.livestock_trade_detail", trade_id=trade_id))


@bp.post("/cash-flow/livestock-trades/<trade_id>/delete")
def delete_livestock_trade(trade_id: str):
    trade = _get_livestock_trade_or_404(trade_id)
    LivestockTradeService.delete_trade(trade)
    db.session.commit()
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
