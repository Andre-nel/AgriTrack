from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.sql import func

from app.extensions import db
from app.models import Farm, GrazingAllocationLsuHistory, Paddock
from app.modules.grazing.constants import (
    GRAZING_ANALYTICS_METRIC_LABELS,
    GRAZING_ANALYTICS_METRIC_ORDER,
    GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES,
    GRAZING_ANALYTICS_POINT_FILTER_OPERATORS,
)
from app.modules.grazing.forms import (
    normalize_grazing_metric_selection,
    normalize_grazing_point_filter_match_mode,
    normalize_grazing_point_filter_metric,
    normalize_grazing_point_filter_operator,
    normalize_grazing_split_mode,
    parse_query_date,
)
from app.modules.grazing.presenters import (
    build_grazing_chart_panels,
    filter_grazing_paddocks_by_point_rule,
    flatten_grazing_periods,
    grazing_paddock_series_label,
    grazing_uncovered_paddocks_in_range,
)
from app.services.grazing_history_service import GrazingHistoryService


def register_legacy_routes(bp) -> None:
    @bp.get("/analytics/grazing-management")
    def analytics_grazing_management():
        selected_filters = {
            "farm_id": (request.args.get("farm_id") or "").strip(),
        }
        requested_paddock_ids = {
            (value or "").strip()
            for value in request.args.getlist("paddock_id")
            if (value or "").strip()
        }
        selected_metrics = normalize_grazing_metric_selection(request.args.getlist("metric"))
        split_mode = normalize_grazing_split_mode(request.args.get("split_mode"))
        point_filter = {
            "metric": normalize_grazing_point_filter_metric(request.args.get("point_filter_metric")),
            "operator": normalize_grazing_point_filter_operator(
                request.args.get("point_filter_operator")
            ),
            "match_mode": normalize_grazing_point_filter_match_mode(
                request.args.get("point_filter_match_mode")
            ),
            "raw_value": (request.args.get("point_filter_value") or "").strip(),
            "value": None,
            "is_active": False,
            "summary": None,
        }
        today = date.today()

        try:
            end_date = parse_query_date(request.args.get("end_date")) or today
            start_date = parse_query_date(request.args.get("start_date"))
        except ValueError:
            flash("Grazing Management dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_grazing_management"))

        if point_filter["raw_value"]:
            try:
                point_filter["value"] = float(Decimal(point_filter["raw_value"]))
                point_filter["is_active"] = True
            except (InvalidOperation, ValueError):
                flash("Trend chart filter value must be a valid number", "error")
                return redirect(url_for("web.analytics_grazing_management"))

        if start_date is not None and end_date < start_date:
            flash("Grazing Management end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_grazing_management"))

        farms = Farm.query.order_by(Farm.name).all()
        paddock_query = Paddock.query.join(Farm).order_by(Farm.name.asc(), Paddock.name.asc())
        if selected_filters["farm_id"]:
            paddock_query = paddock_query.filter(Paddock.farm_id == selected_filters["farm_id"])
        scope_paddocks = paddock_query.all()
        valid_scope_ids = {str(paddock.id) for paddock in scope_paddocks}

        if requested_paddock_ids:
            selected_paddocks = [
                paddock
                for paddock in scope_paddocks
                if str(paddock.id) in requested_paddock_ids & valid_scope_ids
            ]
            if not selected_paddocks:
                selected_paddocks = scope_paddocks
        else:
            selected_paddocks = scope_paddocks

        earliest_covered_row = None
        if selected_paddocks:
            earliest_covered_row = (
                db.session.query(func.min(GrazingAllocationLsuHistory.effective_from))
                .filter(
                    GrazingAllocationLsuHistory.paddock_id.in_(
                        [str(paddock.id) for paddock in selected_paddocks]
                    )
                )
                .scalar()
            )

        if start_date is None:
            rolling_start = today - timedelta(days=364)
            if earliest_covered_row is not None:
                start_date = max(rolling_start, earliest_covered_row.date())
            else:
                start_date = rolling_start

        analytics_payload = GrazingHistoryService.build_paddock_daily_metrics(
            selected_paddocks,
            start_date=start_date,
            end_date=end_date,
        )
        visible_paddocks = filter_grazing_paddocks_by_point_rule(
            selected_paddocks,
            analytics_payload,
            metric=point_filter["metric"],
            operator=point_filter["operator"],
            threshold_value=point_filter["value"],
            match_mode=point_filter["match_mode"],
        )
        if point_filter["is_active"]:
            metric_label = GRAZING_ANALYTICS_METRIC_LABELS[point_filter["metric"]]
            operator_label = GRAZING_ANALYTICS_POINT_FILTER_OPERATORS[
                point_filter["operator"]
            ].lower()
            match_text = (
                "with any matching points"
                if point_filter["match_mode"] == "has_any"
                else "with no matching points"
            )
            point_filter["summary"] = (
                f"Showing {len(visible_paddocks)} of {len(selected_paddocks)} paddock(s) "
                f"{match_text} for {metric_label} {operator_label} "
                f"{point_filter['raw_value']} in the selected date range."
            )

        timeline_periods, initial_period = flatten_grazing_periods(
            visible_paddocks,
            analytics_payload,
        )
        chart_panels = build_grazing_chart_panels(
            paddocks=visible_paddocks,
            analytics_payload=analytics_payload,
            selected_metrics=selected_metrics,
            split_mode=split_mode,
        )
        uncovered_paddocks = grazing_uncovered_paddocks_in_range(
            visible_paddocks,
            analytics_payload,
            start_date=start_date,
        )

        include_farm_name = len({str(paddock.farm_id) for paddock in scope_paddocks}) > 1
        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            "paddocks": [
                {
                    "id": str(paddock.id),
                    "label": grazing_paddock_series_label(paddock, include_farm_name),
                    "farm_name": paddock.farm.name,
                }
                for paddock in scope_paddocks
            ],
            "metrics": [
                {"value": metric, "label": GRAZING_ANALYTICS_METRIC_LABELS[metric]}
                for metric in GRAZING_ANALYTICS_METRIC_ORDER
            ],
            "point_filter_operators": [
                {"value": value, "label": label}
                for value, label in GRAZING_ANALYTICS_POINT_FILTER_OPERATORS.items()
            ],
            "point_filter_match_modes": [
                {"value": value, "label": label}
                for value, label in GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES.items()
            ],
            "split_modes": [
                {"value": "metric", "label": "Split By Metric"},
                {"value": "paddock", "label": "Split By Paddock"},
            ],
        }
        timeline_rows = [
            {
                "paddock_id": str(paddock.id),
                "paddock_name": grazing_paddock_series_label(paddock, include_farm_name),
                "segments": analytics_payload["paddocks"].get(str(paddock.id), {}).get(
                    "periods",
                    [],
                ),
            }
            for paddock in visible_paddocks
        ]
        chart_payload = {
            "labels": analytics_payload["labels"],
            "split_mode": split_mode,
            "panels": chart_panels,
        }

        return render_template(
            "analytics/grazing_management.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            selected_paddock_ids=[str(paddock.id) for paddock in selected_paddocks],
            selected_metrics=selected_metrics,
            split_mode=split_mode,
            point_filter=point_filter,
            start_date=start_date,
            end_date=end_date,
            chart_payload=chart_payload,
            timeline_rows=timeline_rows,
            timeline_ticks=analytics_payload["ticks"],
            initial_period=initial_period,
            uncovered_paddocks=uncovered_paddocks,
            total_periods=len(timeline_periods),
            selected_paddock_count=len(selected_paddocks),
            visible_paddock_count=len(visible_paddocks),
        )
