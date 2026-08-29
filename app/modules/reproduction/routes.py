from datetime import date, timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    BreedingCycle,
    BreedingCycleFarm,
    BreedingEnrollment,
    Farm,
    Mob,
)
from app.services.cohort_service import CohortService
from app.services.reproduction_service import ReproductionService


bp = Blueprint("reproduction", __name__, url_prefix="/reproduction")


def _cycle_or_404(cycle_id: str) -> BreedingCycle:
    return (
        BreedingCycle.query.options(
            selectinload(BreedingCycle.farm_links).selectinload(BreedingCycleFarm.farm),
            selectinload(BreedingCycle.enrollments).selectinload(BreedingEnrollment.cohort),
            selectinload(BreedingCycle.enrollments).selectinload(BreedingEnrollment.source_mob),
            selectinload(BreedingCycle.enrollments).selectinload(
                BreedingEnrollment.pregnancy_assessments
            ),
            selectinload(BreedingCycle.enrollments).selectinload(
                BreedingEnrollment.parturition_outcomes
            ),
            selectinload(BreedingCycle.enrollments).selectinload(
                BreedingEnrollment.offspring_assessments
            ),
            selectinload(BreedingCycle.enrollments).selectinload(BreedingEnrollment.exceptions),
        )
        .filter_by(id=cycle_id)
        .first_or_404()
    )


def _enrollment_or_404(cycle_id: str, enrollment_id: str) -> BreedingEnrollment:
    return BreedingEnrollment.query.filter_by(
        id=enrollment_id, cycle_id=cycle_id
    ).first_or_404()


def _female_balance_options(cycle: BreedingCycle) -> list[dict]:
    rows = (
        AnimalGroupBalance.query.join(Mob, AnimalGroupBalance.mob_id == Mob.id)
        .filter(Mob.farm_id.in_(cycle.farm_ids), AnimalGroupBalance.head_count > 0)
        .all()
    )
    options = []
    enrolled_descendants = set()
    for enrollment in cycle.enrollments:
        enrolled_descendants.update(CohortService.descendants(str(enrollment.cohort_id)))
    for balance in rows:
        group = balance.animal_group_type
        if group.species != cycle.species or not CohortService.is_female_group(group):
            continue
        if balance.cohort_id and str(balance.cohort_id) in enrolled_descendants:
            continue
        options.append(
            {
                "id": str(balance.id),
                "mob_name": f"{balance.mob.farm.name} / {balance.mob.name}",
                "head_count": int(balance.head_count),
                "label": CohortService.display_label(balance.cohort, group),
            }
        )
    return sorted(options, key=lambda row: (row["mob_name"].lower(), row["label"].lower()))


def _sire_cohort_options(cycle: BreedingCycle) -> list[dict]:
    rows = (
        AnimalGroupBalance.query.join(Mob, AnimalGroupBalance.mob_id == Mob.id)
        .join(AnimalCohort, AnimalGroupBalance.cohort_id == AnimalCohort.id)
        .filter(Mob.farm_id.in_(cycle.farm_ids), AnimalGroupBalance.head_count > 0)
        .all()
    )
    options = []
    for balance in rows:
        group = balance.animal_group_type
        if group.species != cycle.species or not CohortService.is_sire_group(group):
            continue
        options.append(
            {
                "id": str(balance.cohort_id),
                "label": f"{balance.mob.farm.name} / {balance.mob.name} | "
                f"{CohortService.display_label(balance.cohort)} "
                f"| {int(balance.head_count)} head",
            }
        )
    return sorted(options, key=lambda row: row["label"].lower())


def _enrollment_rows(cycle: BreedingCycle) -> list[dict]:
    rows = []
    for enrollment in cycle.enrollments:
        scan = ReproductionService.latest_effective(
            enrollment.pregnancy_assessments, "assessed_on"
        )
        birth = ReproductionService.latest_effective(
            enrollment.parturition_outcomes, "period_start_date"
        )
        assessments = sorted(
            ReproductionService._effective(enrollment.offspring_assessments),
            key=lambda row: (row.assessed_on, row.stage, str(row.id)),
            reverse=True,
        )
        exceptions = sorted(
            ReproductionService._effective(enrollment.exceptions),
            key=lambda row: (row.observed_on, str(row.id)),
            reverse=True,
        )
        descendant_ids = CohortService.descendants(str(enrollment.cohort_id))
        current_balances = AnimalGroupBalance.query.filter(
            AnimalGroupBalance.cohort_id.in_(descendant_ids),
            AnimalGroupBalance.head_count > 0,
        ).all()
        current_states = {}
        for balance in current_balances:
            state = balance.cohort.reproductive_state if balance.cohort else "not_recorded"
            lactation = balance.cohort.lactation_state if balance.cohort else "not_recorded"
            at_foot = balance.cohort.offspring_at_foot if balance.cohort else "not_recorded"
            key = (state, lactation, at_foot)
            current_states[key] = current_states.get(key, 0) + int(balance.head_count)
        rows.append(
            {
                "enrollment": enrollment,
                "group_label": CohortService.display_label(enrollment.cohort),
                "scan": scan,
                "birth": birth,
                "assessments": assessments,
                "exceptions": exceptions,
                "current_states": [
                    {
                        "state": state.replace("_", " ").title(),
                        "lactation": lactation.replace("_", " ").title(),
                        "offspring": at_foot.replace("_", " ").title(),
                        "count": count,
                    }
                    for (state, lactation, at_foot), count in sorted(current_states.items())
                ],
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["enrollment"].source_mob.name.lower(),
            row["group_label"].lower(),
        ),
    )


@bp.get("")
def index():
    cycles = BreedingCycle.query.options(
        selectinload(BreedingCycle.farm_links).selectinload(BreedingCycleFarm.farm)
    ).order_by(BreedingCycle.exposure_start_date.desc(), BreedingCycle.name.asc()).all()
    return render_template(
        "reproduction/index.html",
        cycles=cycles,
        farms=Farm.query.filter_by(active=True).order_by(Farm.name.asc()).all(),
        today=date.today().isoformat(),
    )


@bp.post("")
def create_cycle():
    try:
        cycle = ReproductionService.create_cycle_from_form(request.form)
        flash("Breeding cycle created", "success")
        return redirect(url_for("reproduction.detail", cycle_id=cycle.id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("reproduction.index"))
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Could not create breeding cycle")
        flash(
            "The breeding cycle could not be saved. Run 'flask db upgrade' "
            "and try again.",
            "error",
        )
        return redirect(url_for("reproduction.index"))


@bp.get("/cycles/<cycle_id>")
def detail(cycle_id: str):
    cycle = _cycle_or_404(cycle_id)
    return render_template(
        "reproduction/detail.html",
        cycle=cycle,
        female_balances=_female_balance_options(cycle),
        sire_cohorts=_sire_cohort_options(cycle),
        offspring_mobs=Mob.query.options(selectinload(Mob.farm))
        .filter(Mob.farm_id.in_(cycle.farm_ids), Mob.status == "active")
        .order_by(Mob.farm_id.asc(), Mob.name.asc())
        .all(),
        enrollment_rows=_enrollment_rows(cycle),
        today=date.today().isoformat(),
    )


@bp.post("/cycles/<cycle_id>/enrollments")
def enroll(cycle_id: str):
    cycle = _cycle_or_404(cycle_id)
    try:
        ReproductionService.enroll_from_form(cycle, request.form)
        flash("Female cohort enrolled and marked as exposed to the sire", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.post("/cycles/<cycle_id>/enrollments/<enrollment_id>/pregnancy")
def record_pregnancy(cycle_id: str, enrollment_id: str):
    enrollment = _enrollment_or_404(cycle_id, enrollment_id)
    try:
        ReproductionService.record_pregnancy_assessment_from_form(enrollment, request.form)
        flash(
            "Pregnancy observation correction recorded"
            if request.form.get("supersedes_id")
            else "Pregnancy assessment recorded",
            "success",
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.post("/cycles/<cycle_id>/enrollments/<enrollment_id>/parturition")
def record_parturition(cycle_id: str, enrollment_id: str):
    enrollment = _enrollment_or_404(cycle_id, enrollment_id)
    try:
        ReproductionService.record_parturition_from_form(enrollment, request.form)
        flash(
            "Parturition observation correction recorded"
            if request.form.get("supersedes_id")
            else "Parturition outcome and live births recorded",
            "success",
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.post("/cycles/<cycle_id>/enrollments/<enrollment_id>/assessments")
def record_assessment(cycle_id: str, enrollment_id: str):
    enrollment = _enrollment_or_404(cycle_id, enrollment_id)
    try:
        ReproductionService.record_offspring_assessment_from_form(enrollment, request.form)
        flash("Offspring assessment recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.post("/cycles/<cycle_id>/enrollments/<enrollment_id>/exceptions")
def record_exception(cycle_id: str, enrollment_id: str):
    enrollment = _enrollment_or_404(cycle_id, enrollment_id)
    try:
        ReproductionService.record_exception_from_form(enrollment, request.form)
        flash("Reproductive exception recorded", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.post("/cycles/<cycle_id>/close")
def close_cycle(cycle_id: str):
    cycle = _cycle_or_404(cycle_id)
    cycle.status = "closed"
    db.session.commit()
    flash("Breeding cycle closed", "success")
    return redirect(url_for("reproduction.detail", cycle_id=cycle_id))


@bp.get("/analytics")
def analytics():
    today = date.today()
    selected_farm_id = (request.args.get("farm_id") or "").strip()
    selected_species = (request.args.get("species") or "").strip()
    try:
        start_date = ReproductionService._date(
            request.args.get("start_date") or (today - timedelta(days=730)).isoformat(),
            "Start date",
        )
        end_date = ReproductionService._date(
            request.args.get("end_date") or today.isoformat(), "End date"
        )
        if end_date < start_date:
            raise ValueError("End date must be on or after the start date")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("reproduction.analytics"))

    query = BreedingCycle.query.options(
        selectinload(BreedingCycle.farm_links).selectinload(BreedingCycleFarm.farm),
        selectinload(BreedingCycle.enrollments).selectinload(BreedingEnrollment.cohort),
        selectinload(BreedingCycle.enrollments).selectinload(BreedingEnrollment.source_mob),
        selectinload(BreedingCycle.enrollments).selectinload(
            BreedingEnrollment.pregnancy_assessments
        ),
        selectinload(BreedingCycle.enrollments).selectinload(
            BreedingEnrollment.parturition_outcomes
        ),
        selectinload(BreedingCycle.enrollments).selectinload(
            BreedingEnrollment.offspring_assessments
        ),
    ).filter(
        BreedingCycle.exposure_start_date >= start_date,
        BreedingCycle.exposure_start_date <= end_date,
    )
    if selected_farm_id:
        query = query.join(
            BreedingCycleFarm, BreedingCycleFarm.cycle_id == BreedingCycle.id
        ).filter(BreedingCycleFarm.farm_id == selected_farm_id)
    if selected_species:
        query = query.filter(BreedingCycle.species == selected_species)
    cycles = query.order_by(BreedingCycle.exposure_start_date.asc()).all()
    return render_template(
        "reproduction/analytics.html",
        report=ReproductionService.build_analytics_report(
            cycles,
            farm_id=selected_farm_id or None,
        ),
        cycles=cycles,
        farms=Farm.query.order_by(Farm.name.asc()).all(),
        selected_farm_id=selected_farm_id,
        selected_species=selected_species,
        start_date=start_date,
        end_date=end_date,
    )
