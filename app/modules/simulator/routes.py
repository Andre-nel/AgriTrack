from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    SimulatorExpense,
    SimulatorFarm,
    SimulatorRevenueAssumption,
    SimulatorScenario,
    SimulatorStockDetail,
)
from app.services.simulator_service import SimulatorService

bp = Blueprint("simulator", __name__, url_prefix="/simulator")


def _scenario_query():
    return SimulatorScenario.query.options(
        selectinload(SimulatorScenario.revenue_assumptions),
        selectinload(SimulatorScenario.farm_targets).selectinload(SimulatorFarm.farm),
        selectinload(SimulatorScenario.stock_details).selectinload(SimulatorStockDetail.farm),
        selectinload(SimulatorScenario.stock_details).selectinload(SimulatorStockDetail.simulator_farm),
        selectinload(SimulatorScenario.expenses).selectinload(SimulatorExpense.farm),
        selectinload(SimulatorScenario.expenses).selectinload(SimulatorExpense.simulator_farm),
    )


def _scenario_or_404(scenario_id: str) -> SimulatorScenario:
    return _scenario_query().filter_by(id=scenario_id).first_or_404()


def _scenario_redirect(scenario: SimulatorScenario):
    return redirect(url_for("simulator.detail", scenario_id=scenario.id))


@bp.get("/")
def index():
    scenarios = SimulatorScenario.query.order_by(
        SimulatorScenario.updated_at.desc(),
        SimulatorScenario.name.asc(),
    ).all()
    return render_template(
        "simulator/index.html",
        scenarios=scenarios,
        farms=SimulatorService.active_farms(),
        current_year=date.today().year,
    )


@bp.post("/scenarios")
def create_scenario():
    try:
        scenario = SimulatorService.create_scenario(request.form)
        db.session.commit()
        flash("Simulator scenario created", "success")
        return _scenario_redirect(scenario)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("simulator.index"))


@bp.get("/scenarios/<scenario_id>")
def detail(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    projection = SimulatorService.build_projection(scenario)
    farm_options = SimulatorService.scenario_farm_options(scenario)
    expense_rows = []
    for expense in sorted(scenario.expenses, key=lambda item: (item.created_at, item.label.lower())):
        monthly_outflow = None
        if expense.expense_type == "standard" and expense.recurrence == "monthly":
            monthly_outflow = expense.amount
        if expense.expense_type == "loan" and int(expense.payment_interval_months or 0) == 1:
            monthly_outflow = SimulatorService.loan_payment_amount(expense)
        expense_rows.append(
            {
                "expense": expense,
                "farm_option_value": str(expense.simulator_farm_id or expense.farm_id or ""),
                "annual": SimulatorService.expense_annual_amount(expense),
                "monthly_outflow": monthly_outflow,
                "payment_amount": SimulatorService.loan_payment_amount(expense)
                if expense.expense_type == "loan"
                else None,
            }
        )
    return render_template(
        "simulator/detail.html",
        scenario=scenario,
        projection=projection,
        expense_rows=expense_rows,
        farm_options=farm_options,
        species_options=SimulatorService.species_options(),
        expense_categories=SimulatorService.expense_category_options(),
        recurrence_options=SimulatorService.recurrence_options(),
        payment_interval_options=SimulatorService.payment_interval_options(),
        month_options=SimulatorService.month_options(),
        format_currency=SimulatorService.format_currency,
        category_label=SimulatorService.category_label,
    )


@bp.post("/scenarios/<scenario_id>/edit")
def edit_scenario(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    try:
        SimulatorService.update_scenario(scenario, request.form)
        db.session.commit()
        flash("Scenario updated", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/delete")
def delete_scenario(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    db.session.delete(scenario)
    db.session.commit()
    flash("Scenario deleted", "success")
    return redirect(url_for("simulator.index"))


@bp.post("/scenarios/<scenario_id>/revenue-assumptions")
def upsert_revenue_assumption(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    try:
        SimulatorService.upsert_revenue_assumption(scenario, request.form)
        db.session.commit()
        flash("Revenue assumption saved", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/revenue-assumptions/<assumption_id>/delete")
def delete_revenue_assumption(scenario_id: str, assumption_id: str):
    scenario = _scenario_or_404(scenario_id)
    assumption = SimulatorRevenueAssumption.query.filter_by(
        id=assumption_id,
        scenario_id=scenario.id,
    ).first_or_404()
    db.session.delete(assumption)
    db.session.commit()
    flash("Revenue assumption removed", "success")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/stock-details")
def upsert_stock_detail(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    try:
        SimulatorService.upsert_stock_detail(scenario, request.form)
        db.session.commit()
        flash("Stock detail saved", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/stock-details/<stock_detail_id>/edit")
def edit_stock_detail(scenario_id: str, stock_detail_id: str):
    scenario = _scenario_or_404(scenario_id)
    stock_detail = SimulatorStockDetail.query.filter_by(
        id=stock_detail_id,
        scenario_id=scenario.id,
    ).first_or_404()
    try:
        SimulatorService.update_stock_detail(stock_detail, request.form)
        db.session.commit()
        flash("Stock detail updated", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/stock-details/<stock_detail_id>/delete")
def delete_stock_detail(scenario_id: str, stock_detail_id: str):
    scenario = _scenario_or_404(scenario_id)
    stock_detail = SimulatorStockDetail.query.filter_by(
        id=stock_detail_id,
        scenario_id=scenario.id,
    ).first_or_404()
    db.session.delete(stock_detail)
    db.session.commit()
    flash("Stock detail removed", "success")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/stock-details/seed")
def seed_stock_details(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    SimulatorService.seed_stock_details_from_live(scenario)
    db.session.commit()
    flash("Stock details refreshed from live adult female counts", "success")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/stock-details/clear")
def clear_stock_details(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    SimulatorService.clear_stock_details(scenario)
    db.session.commit()
    flash("Stock details cleared", "success")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/expenses")
def create_expense(scenario_id: str):
    scenario = _scenario_or_404(scenario_id)
    try:
        SimulatorService.create_expense(scenario, request.form)
        db.session.commit()
        flash("Expense saved", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/expenses/<expense_id>/edit")
def edit_expense(scenario_id: str, expense_id: str):
    scenario = _scenario_or_404(scenario_id)
    expense = SimulatorExpense.query.filter_by(
        id=expense_id,
        scenario_id=scenario.id,
    ).first_or_404()
    try:
        SimulatorService.update_expense(expense, scenario, request.form)
        db.session.commit()
        flash("Expense updated", "success")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _scenario_redirect(scenario)


@bp.post("/scenarios/<scenario_id>/expenses/<expense_id>/delete")
def delete_expense(scenario_id: str, expense_id: str):
    scenario = _scenario_or_404(scenario_id)
    expense = SimulatorExpense.query.filter_by(
        id=expense_id,
        scenario_id=scenario.id,
    ).first_or_404()
    db.session.delete(expense)
    db.session.commit()
    flash("Expense removed", "success")
    return _scenario_redirect(scenario)
