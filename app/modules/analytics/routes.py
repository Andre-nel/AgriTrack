from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import AnimalGroupType, Farm, Paddock, ShearingBaleCode, StockLedgerEntry, WaterAsset
from app.modules.analytics.constants import (
    ANALYTICS_GROUP_LABELS,
    ANALYTICS_GROUP_ORDER,
    LSU_PADDOCK_TRACKING_METRIC_LABELS,
    LSU_PADDOCK_TRACKING_METRIC_ORDER,
    LSU_PADDOCK_TRACKING_PLOT_MODES,
    WATER_ASSET_ANALYTICS_PLOT_MODES,
    WATER_ASSET_CURRENT_ACTIVE_FILTERS,
    WATER_ASSET_STATE_FIELD_LABELS,
    WATER_ASSET_STATE_FIELD_ORDER,
)
from app.modules.analytics.forms import (
    journal_return_query_args,
    normalize_analytics_group_by,
    normalize_lsu_paddock_tracking_metric,
    normalize_lsu_paddock_tracking_plot_mode,
    normalize_repeated_query_ids,
    normalize_shearing_analytics_species,
    normalize_water_asset_analytics_asset_types,
    normalize_water_asset_analytics_plot_mode,
    normalize_water_asset_current_active_filter,
    normalize_water_asset_state_fields,
    parse_query_date,
)
from app.modules.analytics.presenters import (
    build_analytics_journal_entries,
    build_stock_tracking_chart_data,
    current_stock_totals_by_group,
    group_analytics_journal_entries,
)
from app.modules.analytics.services import (
    build_lsu_paddock_tracking_report,
    build_shearing_analytics_report,
    build_water_asset_state_report,
    create_journal_entry,
    earliest_lsu_paddock_breakdown_date,
    earliest_shearing_session_date,
    latest_shearing_session_date,
    lsu_paddock_breakdown_uncovered_paddocks,
)
from app.services.water_network_service import WaterNetworkService


def _choice_filter_label(value: str) -> str:
    return value.replace("_", " ").title()


def _water_asset_filter_label(asset: WaterAsset, include_farm_name: bool) -> str:
    if include_farm_name and asset.farm:
        return f"{asset.farm.name} | {asset.name}"
    return asset.name


def _truthy_query_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _format_optional_number(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{decimals}f}"


def _format_count(value) -> str:
    if value is None:
        return "N/A"
    return f"{int(value):,}"


def _format_money(value) -> str:
    if value is None:
        return "N/A"
    return f"R {float(value):,.2f}"


def _format_rate(value) -> str:
    if value is None:
        return "N/A"
    return f"R {float(value):,.4f}"


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

    @bp.get("/analytics/water-assets")
    def analytics_water_assets():
        selected_filters = {
            "farm_id": (request.args.get("farm_id") or "").strip(),
            "current_status": (request.args.get("current_status") or "").strip(),
            "current_water_level": (request.args.get("current_water_level") or "").strip(),
        }
        selected_asset_types = normalize_water_asset_analytics_asset_types(
            request.args.getlist("asset_type")
        )
        requested_water_asset_ids = {
            (value or "").strip()
            for value in request.args.getlist("water_asset_id")
            if (value or "").strip()
        }
        current_active = normalize_water_asset_current_active_filter(
            request.args.get("current_active")
        )
        state_fields = normalize_water_asset_state_fields(request.args.getlist("state_field"))
        plot_mode = normalize_water_asset_analytics_plot_mode(request.args.get("plot_mode"))
        today = date.today()

        try:
            start_date = parse_query_date(request.args.get("start_date")) or (
                today - timedelta(days=364)
            )
            end_date = parse_query_date(request.args.get("end_date")) or today
        except ValueError:
            flash("Water asset analytics dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_water_assets"))

        if end_date < start_date:
            flash("Water asset analytics end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_water_assets"))

        farms = Farm.query.order_by(Farm.name).all()
        asset_query = WaterAsset.query.join(Farm).order_by(
            Farm.name.asc(),
            WaterAsset.asset_type.asc(),
            WaterAsset.name.asc(),
        )
        if selected_filters["farm_id"]:
            asset_query = asset_query.filter(WaterAsset.farm_id == selected_filters["farm_id"])
        if selected_asset_types:
            asset_query = asset_query.filter(WaterAsset.asset_type.in_(selected_asset_types))
        if current_active == "active":
            asset_query = asset_query.filter(WaterAsset.active.is_(True))
        elif current_active == "inactive":
            asset_query = asset_query.filter(WaterAsset.active.is_(False))
        if selected_filters["current_status"]:
            asset_query = asset_query.filter(WaterAsset.status == selected_filters["current_status"])
        if selected_filters["current_water_level"]:
            asset_query = asset_query.filter(
                WaterAsset.water_level == selected_filters["current_water_level"]
            )

        scope_assets = asset_query.all()
        valid_scope_ids = {str(asset.id) for asset in scope_assets}
        if requested_water_asset_ids:
            selected_assets = [
                asset
                for asset in scope_assets
                if str(asset.id) in requested_water_asset_ids & valid_scope_ids
            ]
            if not selected_assets:
                selected_assets = scope_assets
        else:
            selected_assets = scope_assets

        selected_water_asset_ids = [str(asset.id) for asset in selected_assets]
        include_farm_name = len({str(asset.farm_id) for asset in scope_assets}) > 1
        report = build_water_asset_state_report(
            assets=selected_assets,
            start_date=start_date,
            end_date=end_date,
            state_fields=state_fields,
            plot_mode=plot_mode,
            include_farm_name=include_farm_name,
        )
        all_status_options = sorted(
            {
                option
                for options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.values()
                for option in options
            }
            | {asset.status for asset in scope_assets if asset.status}
        )

        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            "asset_types": [
                {
                    "value": asset_type,
                    "label": WaterNetworkService.ASSET_TYPE_LABELS.get(asset_type, asset_type),
                }
                for asset_type in WaterNetworkService.ASSET_TYPES
            ],
            "assets": [
                {
                    "id": str(asset.id),
                    "label": _water_asset_filter_label(asset, include_farm_name),
                    "farm_name": asset.farm.name if asset.farm else "",
                }
                for asset in scope_assets
            ],
            "current_active_options": [
                {"value": value, "label": label}
                for value, label in WATER_ASSET_CURRENT_ACTIVE_FILTERS.items()
            ],
            "statuses": [
                {"value": value, "label": _choice_filter_label(value)}
                for value in all_status_options
            ],
            "water_levels": [
                {"value": value, "label": _choice_filter_label(value)}
                for value in WaterNetworkService.WATER_LEVEL_OPTIONS
            ],
            "state_fields": [
                {"value": value, "label": WATER_ASSET_STATE_FIELD_LABELS[value]}
                for value in WATER_ASSET_STATE_FIELD_ORDER
            ],
            "plot_modes": [
                {"value": value, "label": label}
                for value, label in WATER_ASSET_ANALYTICS_PLOT_MODES.items()
            ],
        }

        return render_template(
            "analytics/water_assets.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            selected_asset_types=selected_asset_types,
            selected_water_asset_ids=selected_water_asset_ids,
            current_active=current_active,
            state_fields=state_fields,
            plot_mode=plot_mode,
            start_date=start_date,
            end_date=end_date,
            chart_payload=report["chart_payload"],
            summary_rows=report["summary_rows"],
            has_history=report["has_history"],
        )

    @bp.get("/analytics/shearing")
    def analytics_shearing():
        farms = Farm.query.order_by(Farm.name).all()
        valid_farm_ids = {str(farm.id) for farm in farms}
        selected_farm_ids = [
            farm_id
            for farm_id in normalize_repeated_query_ids(request.args.getlist("farm_id"))
            if farm_id in valid_farm_ids
        ]
        selected_species = normalize_shearing_analytics_species(request.args.get("species"))
        group_by_farm = _truthy_query_flag(request.args.get("group_by_farm"))
        today = date.today()
        rolling_start = today - timedelta(days=730)
        earliest_session = earliest_shearing_session_date(
            farm_ids=selected_farm_ids,
            species=selected_species,
        )
        latest_session = latest_shearing_session_date(
            farm_ids=selected_farm_ids,
            species=selected_species,
        )
        default_start = (
            max(rolling_start, earliest_session)
            if earliest_session is not None
            else rolling_start
        )
        default_end = max(today, latest_session) if latest_session is not None else today

        try:
            start_date = parse_query_date(request.args.get("start_date")) or default_start
            end_date = parse_query_date(request.args.get("end_date")) or default_end
        except ValueError:
            flash("Shearing analytics dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_shearing"))

        if end_date < start_date:
            flash("Shearing analytics end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_shearing"))

        bale_code_query = ShearingBaleCode.query
        if selected_species:
            bale_code_query = bale_code_query.filter(ShearingBaleCode.species == selected_species)
        bale_codes = bale_code_query.order_by(
            ShearingBaleCode.species.asc(),
            ShearingBaleCode.code.asc(),
        ).all()
        valid_bale_code_ids = {str(code.id) for code in bale_codes}
        selected_bale_code_ids = [
            code_id
            for code_id in normalize_repeated_query_ids(request.args.getlist("bale_code_id"))
            if code_id in valid_bale_code_ids
        ]

        report = build_shearing_analytics_report(
            farm_ids=selected_farm_ids,
            species=selected_species,
            start_date=start_date,
            end_date=end_date,
            bale_code_ids=selected_bale_code_ids,
            group_by_farm=group_by_farm,
        )
        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            "species": ["Sheep", "Goat"],
            "bale_codes": [
                {
                    "id": str(code.id),
                    "species": code.species,
                    "code": code.code,
                    "label": code.code if selected_species else f"{code.species} | {code.code}",
                }
                for code in bale_codes
            ],
        }
        selected_filters = {"species": selected_species}

        return render_template(
            "analytics/shearing.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            selected_farm_ids=selected_farm_ids,
            selected_bale_code_ids=selected_bale_code_ids,
            group_by_farm=group_by_farm,
            start_date=start_date,
            end_date=end_date,
            chart_payload=report["chart_payload"],
            summary=report["summary"],
            session_rows=report["session_rows"],
            code_rows=report["code_rows"],
            has_sessions=report["has_sessions"],
            format_count=_format_count,
            format_money=_format_money,
            format_rate=_format_rate,
            format_number=_format_optional_number,
        )

    @bp.get("/analytics/lsu-paddock-tracking")
    def analytics_lsu_paddock_tracking():
        selected_filters = {
            "farm_id": (request.args.get("farm_id") or "").strip(),
            "species": (request.args.get("species") or "").strip(),
        }
        requested_paddock_ids = {
            (value or "").strip()
            for value in request.args.getlist("paddock_id")
            if (value or "").strip()
        }
        metric = normalize_lsu_paddock_tracking_metric(request.args.get("metric"))
        plot_mode = normalize_lsu_paddock_tracking_plot_mode(request.args.get("plot_mode"))
        min_value_raw = (request.args.get("min_value") or "").strip()
        min_value = None
        group_by_species = (request.args.get("group_by_species") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        today = date.today()

        try:
            start_date = parse_query_date(request.args.get("start_date"))
            end_date = parse_query_date(request.args.get("end_date")) or today
        except ValueError:
            flash("LSU Paddock Tracking dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.analytics_lsu_paddock_tracking"))

        if start_date and end_date < start_date:
            flash("LSU Paddock Tracking end date must be on or after the start date", "error")
            return redirect(url_for("web.analytics_lsu_paddock_tracking"))

        if min_value_raw:
            try:
                min_value = float(Decimal(min_value_raw))
            except (InvalidOperation, ValueError):
                flash("Minimum metric value must be a valid number", "error")
                return redirect(url_for("web.analytics_lsu_paddock_tracking"))
            if min_value < 0:
                flash("Minimum metric value must be zero or greater", "error")
                return redirect(url_for("web.analytics_lsu_paddock_tracking"))

        farms = Farm.query.order_by(Farm.name).all()
        group_types = AnimalGroupType.query.order_by(AnimalGroupType.species.asc()).all()
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

        selected_paddock_ids = [str(paddock.id) for paddock in selected_paddocks]
        earliest_breakdown_date = earliest_lsu_paddock_breakdown_date(
            paddock_ids=selected_paddock_ids,
            species=selected_filters["species"],
        )
        if start_date is None:
            rolling_start = today - timedelta(days=364)
            start_date = (
                max(rolling_start, earliest_breakdown_date)
                if earliest_breakdown_date is not None
                else rolling_start
            )

        include_farm_name = len({str(paddock.farm_id) for paddock in scope_paddocks}) > 1
        report = build_lsu_paddock_tracking_report(
            paddocks=selected_paddocks,
            start_date=start_date,
            end_date=end_date,
            species=selected_filters["species"],
            metric=metric,
            plot_mode=plot_mode,
            group_by_species=group_by_species,
            include_farm_name=include_farm_name,
            min_value=min_value,
        )
        uncovered_paddocks = lsu_paddock_breakdown_uncovered_paddocks(
            paddocks=selected_paddocks,
            start_date=start_date,
            species=selected_filters["species"],
            include_farm_name=include_farm_name,
        )

        filter_options = {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            "species": sorted({group.species for group in group_types}),
            "paddocks": [
                {
                    "id": str(paddock.id),
                    "label": (
                        f"{paddock.farm.name} | {paddock.name}"
                        if include_farm_name
                        else paddock.name
                    ),
                    "farm_name": paddock.farm.name,
                }
                for paddock in scope_paddocks
            ],
            "metrics": [
                {"value": metric_value, "label": LSU_PADDOCK_TRACKING_METRIC_LABELS[metric_value]}
                for metric_value in LSU_PADDOCK_TRACKING_METRIC_ORDER
            ],
            "plot_modes": [
                {"value": value, "label": label}
                for value, label in LSU_PADDOCK_TRACKING_PLOT_MODES.items()
            ],
        }

        return render_template(
            "analytics/lsu_paddock_tracking.html",
            filter_options=filter_options,
            selected_filters=selected_filters,
            selected_paddock_ids=selected_paddock_ids,
            metric=metric,
            plot_mode=plot_mode,
            group_by_species=group_by_species,
            min_value_raw=min_value_raw,
            start_date=start_date,
            end_date=end_date,
            chart_payload=report["chart_payload"],
            summary_rows=report["summary_rows"],
            has_history=report["has_history"],
            uncovered_paddocks=uncovered_paddocks,
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
