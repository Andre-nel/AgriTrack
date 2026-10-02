"""Shared stock history queries and page report for analytics and a single mob."""

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, StockLedgerEntry
from app.modules.analytics.constants import ANALYTICS_GROUP_LABELS, ANALYTICS_GROUP_ORDER
from app.modules.analytics.forms import (
    normalize_analytics_group_by,
    normalize_repeated_query_ids,
    parse_query_date,
)
from app.modules.analytics.presenters import (
    build_stock_tracking_chart_data,
    current_stock_totals_by_group,
)


def build_stock_tracking_report(query_args: MultiDict, *, mob_id: str | None = None) -> dict:
    """Build the same filtered report for either page, keeping mob scope immutable."""
    today = datetime.now(timezone.utc).date()
    try:
        start_date = parse_query_date(query_args.get("start_date"))
        end_date = parse_query_date(query_args.get("end_date")) or today
        period_end = datetime.combine(end_date + timedelta(days=1), time.min)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Analytics dates must be valid (YYYY-MM-DD) and before 9999-12-31") from exc
    if start_date and end_date < start_date:
        raise ValueError("Analytics end date must be on or after the start date")

    farms = Farm.query.order_by(Farm.name).all() if mob_id is None else []
    valid_farm_ids = {str(farm.id) for farm in farms}
    selected_farm_ids = [
        farm_id
        for farm_id in normalize_repeated_query_ids(query_args.getlist("farm_id"))
        if farm_id in valid_farm_ids
    ]
    dimension_fields = ("species", "breed", "sex", "age_class")
    selected_filters = {
        "farm_ids": selected_farm_ids,
        **{field: (query_args.get(field) or "").strip() for field in dimension_fields},
    }
    group_fields = [field for field in ANALYTICS_GROUP_ORDER if mob_id is None or field != "farm"]
    requested_groups = query_args.getlist("group_by")
    if mob_id is not None:
        requested_groups = [field for field in requested_groups if field.strip().lower() != "farm"]
    selected_group_by = normalize_analytics_group_by(
        requested_groups, use_default=query_args.get("group_by_applied") != "1"
    )

    group_query = AnimalGroupType.query
    if mob_id is not None:
        historical_group_ids = db.session.query(StockLedgerEntry.animal_group_type_id).filter(
            StockLedgerEntry.mob_id == mob_id
        )
        current_group_ids = db.session.query(AnimalGroupBalance.animal_group_type_id).filter(
            AnimalGroupBalance.mob_id == mob_id
        )
        group_query = group_query.filter(
            or_(
                AnimalGroupType.id.in_(historical_group_ids),
                AnimalGroupType.id.in_(current_group_ids),
            )
        )
    group_types = group_query.order_by(
        *(getattr(AnimalGroupType, field) for field in dimension_fields)
    ).all()

    ledger_query = (
        db.session.query(StockLedgerEntry, Farm.name.label("farm_name"), AnimalGroupType)
        .join(Farm, StockLedgerEntry.farm_id == Farm.id)
        .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
        .options(joinedload(StockLedgerEntry.mob))
        .filter(StockLedgerEntry.event_time < period_end)
    )
    if mob_id is not None:
        # A mob may have moved farms since its older ledger entries were recorded.
        ledger_query = ledger_query.filter(StockLedgerEntry.mob_id == mob_id)
    elif selected_farm_ids:
        ledger_query = ledger_query.filter(StockLedgerEntry.farm_id.in_(selected_farm_ids))
    for field in dimension_fields:
        if selected_filters[field]:
            ledger_query = ledger_query.filter(
                getattr(AnimalGroupType, field) == selected_filters[field]
            )
    ledger_rows = ledger_query.order_by(
        StockLedgerEntry.event_time.asc(), StockLedgerEntry.id.asc()
    ).all()

    if start_date is None:
        start_date = (
            ledger_rows[0][0].event_time.date()
            if ledger_rows
            else end_date - timedelta(days=min(30, end_date.toordinal() - 1))
        )
    current_totals = None
    if end_date >= today:
        totals = current_stock_totals_by_group(
            selected_filters=selected_filters,
            group_by_fields=selected_group_by,
            mob_id=mob_id,
        )
        # An empty balance on the dedicated mob page is an explicit current zero.
        # Keep analytics' existing behavior when no current balances are available.
        if totals or mob_id is not None:
            current_totals = totals
    chart_data, latest_totals = build_stock_tracking_chart_data(
        ledger_rows=ledger_rows,
        group_by_fields=selected_group_by,
        start_date=start_date,
        end_date=end_date,
        reconcile_to_current_totals=current_totals,
    )
    return {
        "filter_options": {
            "farms": [{"id": str(farm.id), "name": farm.name} for farm in farms],
            **{
                field: sorted({getattr(group, field) for group in group_types})
                for field in dimension_fields
            },
        },
        "selected_filters": selected_filters,
        "selected_farm_ids": selected_farm_ids,
        "group_by_options": [
            {"value": field, "label": ANALYTICS_GROUP_LABELS[field]} for field in group_fields
        ],
        "selected_group_by": selected_group_by,
        "start_date": start_date,
        "end_date": end_date,
        "chart_data": chart_data,
        "latest_totals": latest_totals,
    }
