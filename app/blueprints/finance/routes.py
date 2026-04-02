from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import CashTransaction, Farm
from app.services.cash_flow_service import CashFlowService
from app.services.finance_service import FinanceService

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

    rows = []
    for transaction in transactions:
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


@bp.get("/cash-flow")
def cash_flow():
    try:
        selected_year = FinanceService.parse_year(request.args.get("year"))
    except ValueError:
        flash("Year must be valid", "error")
        return redirect(url_for("finance.cash_flow"))

    selected_tab = CashFlowService.normalize_tab(request.args.get("tab"))
    dashboard = CashFlowService.build_dashboard(selected_year)
    farms = Farm.query.order_by(Farm.name).all()

    return render_template(
        "finance/cash_flow.html",
        selected_year=selected_year,
        selected_tab=selected_tab,
        years=FinanceService.available_years(selected_year),
        farms=farms,
        dashboard=dashboard,
        active_statement=dashboard["statements"][selected_tab],
        category_groups=FinanceService.category_select_groups(),
        species_scope_options=FinanceService.species_scope_options(),
        new_line_rows=_build_form_line_rows(),
        recent_transactions=_build_transaction_rows(selected_year),
        format_currency=FinanceService.format_currency,
    )


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
    try:
        year = FinanceService.parse_year(request.form.get("return_year"), default=date.today().year)
    except ValueError:
        year = date.today().year
    tab = CashFlowService.normalize_tab(request.form.get("return_tab"))
    FinanceService.delete_transaction(transaction)
    db.session.commit()
    flash("Cash transaction deleted", "success")
    return _cash_flow_redirect(year=year, tab=tab)
