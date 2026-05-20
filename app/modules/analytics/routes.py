from datetime import date, datetime, time, timedelta

from flask import flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import AnimalGroupType, Farm, StockLedgerEntry
from app.modules.analytics.constants import ANALYTICS_GROUP_LABELS, ANALYTICS_GROUP_ORDER
from app.modules.analytics.forms import (
    journal_return_query_args,
    normalize_analytics_group_by,
    parse_query_date,
)
from app.modules.analytics.presenters import (
    build_analytics_journal_entries,
    build_stock_tracking_chart_data,
    current_stock_totals_by_group,
    group_analytics_journal_entries,
)
from app.modules.analytics.services import create_journal_entry


def register_legacy_routes(bp) -> None:
    @bp.get("/analytics")
    def analytics_landing():
        return render_template("analytics/index.html")

    @bp.get("/analytics/journal")
    def analytics_journal():
        selected_filters = {
            "farm_id": (request.args.get("farm_id") or "").strip(),
            "tag": (request.args.get("tag") or "").strip(),
        }
        today = date.today()

        try:
            start_date = parse_query_date(request.args.get("start_date")) or (
                today - timedelta(days=30)
            )
            end_date = parse_query_date(request.args.get("end_date")) or today
        except ValueError:
            flash("Journal dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_journal"))

        if end_date < start_date:
            flash("Journal end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_journal"))

        farms = Farm.query.order_by(Farm.name).all()
        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
        }

        entries = build_analytics_journal_entries(
            farm_id=selected_filters["farm_id"],
            start_date=start_date,
            end_date=end_date,
        )
        journal_days = group_analytics_journal_entries(
            entries=entries,
            selected_tag=selected_filters["tag"],
        )
        total_entries = sum(len(day["entries"]) for day in journal_days)

        return render_template(
            "analytics/journal.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            start_date=start_date,
            end_date=end_date,
            journal_new_entry_event_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M"),
            journal_days=journal_days,
            total_entries=total_entries,
        )

    @bp.get("/analytics/stock-tracking")
    def analytics_stock_tracking():
        selected_filters = {
            "farm_id": (request.args.get("farm_id") or "").strip(),
            "species": (request.args.get("species") or "").strip(),
            "breed": (request.args.get("breed") or "").strip(),
            "sex": (request.args.get("sex") or "").strip(),
            "age_class": (request.args.get("age_class") or "").strip(),
        }
        selected_group_by = normalize_analytics_group_by(request.args.getlist("group_by"))
        today = date.today()

        try:
            start_date = parse_query_date(request.args.get("start_date"))
            end_date = parse_query_date(request.args.get("end_date")) or today
        except ValueError:
            flash("Analytics dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_stock_tracking"))

        if start_date and end_date < start_date:
            flash("Analytics end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_stock_tracking"))

        farms = Farm.query.order_by(Farm.name).all()
        group_types = AnimalGroupType.query.order_by(
            AnimalGroupType.species,
            AnimalGroupType.breed,
            AnimalGroupType.sex,
            AnimalGroupType.age_class,
        ).all()
        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            "species": sorted({group.species for group in group_types}),
            "breed": sorted({group.breed for group in group_types}),
            "sex": sorted({group.sex for group in group_types}),
            "age_class": sorted({group.age_class for group in group_types}),
        }
        group_by_options = [
            {"value": field, "label": ANALYTICS_GROUP_LABELS[field]}
            for field in ANALYTICS_GROUP_ORDER
        ]

        ledger_query = (
            db.session.query(StockLedgerEntry, Farm.name.label("farm_name"), AnimalGroupType)
            .join(Farm, StockLedgerEntry.farm_id == Farm.id)
            .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
        )

        if selected_filters["farm_id"]:
            ledger_query = ledger_query.filter(StockLedgerEntry.farm_id == selected_filters["farm_id"])
        if selected_filters["species"]:
            ledger_query = ledger_query.filter(AnimalGroupType.species == selected_filters["species"])
        if selected_filters["breed"]:
            ledger_query = ledger_query.filter(AnimalGroupType.breed == selected_filters["breed"])
        if selected_filters["sex"]:
            ledger_query = ledger_query.filter(AnimalGroupType.sex == selected_filters["sex"])
        if selected_filters["age_class"]:
            ledger_query = ledger_query.filter(
                AnimalGroupType.age_class == selected_filters["age_class"]
            )

        period_end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
        ledger_rows = (
            ledger_query.filter(StockLedgerEntry.event_time < period_end_dt)
            .order_by(StockLedgerEntry.event_time.asc(), StockLedgerEntry.id.asc())
            .all()
        )

        if start_date is None:
            if ledger_rows:
                start_date = ledger_rows[0][0].event_time.date()
            else:
                start_date = end_date - timedelta(days=30)

        reconcile_to_current_totals = None
        if end_date >= today:
            current_totals = current_stock_totals_by_group(
                selected_filters=selected_filters,
                group_by_fields=selected_group_by,
            )
            if current_totals:
                reconcile_to_current_totals = current_totals

        chart_data, latest_totals = build_stock_tracking_chart_data(
            ledger_rows=ledger_rows,
            group_by_fields=selected_group_by,
            start_date=start_date,
            end_date=end_date,
            reconcile_to_current_totals=reconcile_to_current_totals,
        )

        return render_template(
            "analytics/stock_tracking.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            group_by_options=group_by_options,
            selected_group_by=selected_group_by,
            start_date=start_date,
            end_date=end_date,
            chart_data=chart_data,
            latest_totals=latest_totals,
        )

    @bp.post("/analytics/journal")
    def analytics_journal_create_form():
        farm_id = (request.form.get("farm_id") or "").strip()
        redirect_kwargs = journal_return_query_args(request.form)

        try:
            create_journal_entry(
                farm_id=farm_id,
                tags_raw=request.form.get("tags"),
                description=request.form.get("description"),
                event_at_raw=request.form.get("event_at"),
            )
            flash("Journal entry recorded", "success")
        except ValueError as exc:
            flash(str(exc), "error")

        return redirect(url_for("web.analytics_journal", **redirect_kwargs))
