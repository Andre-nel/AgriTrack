from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    AnimalGroupType,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    GrazingSession,
    Mob,
    Paddock,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.reporting_service import ReportingService


class GrazingHistoryService:
    SOURCE_BACKFILL = "backfill_ledger"
    SOURCE_LIVE = "live"
    GREEN = "#2f7a5c"
    BLUE = "#2c6fb0"
    RED = "#c53b3b"
    STOCK_IN_TYPES = {
        StockEventType.birth,
        StockEventType.purchase,
        StockEventType.transfer_in,
        StockEventType.adjustment_in,
    }
    STOCK_OUT_TYPES = {
        StockEventType.death,
        StockEventType.sale,
        StockEventType.missing,
        StockEventType.transfer_out,
        StockEventType.adjustment_out,
    }

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _history_values_match(
        cls,
        row: GrazingAllocationLsuHistory,
        *,
        allocation_fraction: float,
        mob_total_lsu: float,
        allocated_lsu: float,
    ) -> bool:
        return (
            abs(float(row.allocation_fraction) - allocation_fraction) < 0.0001
            and abs(float(row.mob_total_lsu) - mob_total_lsu) < 0.0001
            and abs(float(row.allocated_lsu) - allocated_lsu) < 0.0001
        )

    @classmethod
    def _close_or_delete_open_row(
        cls, row: GrazingAllocationLsuHistory, effective_to: datetime
    ) -> None:
        close_at = cls._normalize_datetime(effective_to)
        if close_at is None:
            return
        row_from = cls._normalize_datetime(row.effective_from)
        if row_from is not None and row_from >= close_at:
            db.session.delete(row)
            return
        row.effective_to = effective_to

    @classmethod
    def paddock_capacity_context(cls, paddock: Paddock) -> dict:
        area_ha = float(paddock.area_ha or 0)
        grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
        effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha
        effective_stocking_rate = float(
            paddock.stocking_rate_ha_per_lsu_override or paddock.farm.default_stocking_rate_ha_per_lsu
        )
        grazing_capacity_sdh = (365.0 / effective_stocking_rate) if effective_stocking_rate > 0 else None
        return {
            "area_ha": area_ha,
            "grazeable_area_ha": grazeable_area_ha,
            "effective_area_ha": effective_area_ha,
            "effective_stocking_rate_ha_per_lsu": effective_stocking_rate,
            "grazing_capacity_sdh": grazing_capacity_sdh,
        }

    @classmethod
    def paddock_lsu_days_for_period(
        cls,
        paddock: Paddock | str,
        *,
        period_start: datetime,
        period_end: datetime,
    ) -> float:
        paddock_id = str(paddock.id) if isinstance(paddock, Paddock) else str(paddock)
        start_dt = cls._normalize_datetime(period_start)
        end_dt = cls._normalize_datetime(period_end)
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            return 0.0

        rows = (
            GrazingAllocationLsuHistory.query.filter(
                GrazingAllocationLsuHistory.paddock_id == paddock_id,
                GrazingAllocationLsuHistory.effective_from < end_dt,
                or_(
                    GrazingAllocationLsuHistory.effective_to.is_(None),
                    GrazingAllocationLsuHistory.effective_to > start_dt,
                ),
            )
            .order_by(GrazingAllocationLsuHistory.effective_from.asc())
            .all()
        )

        lsu_days = 0.0
        for row in rows:
            row_start = cls._normalize_datetime(row.effective_from)
            row_end = cls._normalize_datetime(row.effective_to) or end_dt
            overlap_start = max(start_dt, row_start)
            overlap_end = min(end_dt, row_end)
            if overlap_end <= overlap_start:
                continue
            duration_days = (overlap_end - overlap_start).total_seconds() / 86400.0
            lsu_days += float(row.allocated_lsu) * duration_days
        return lsu_days

    @classmethod
    def timeline_intensity_ratio(
        cls,
        *,
        avg_lsu_per_ha: float | None,
        effective_stocking_rate_ha_per_lsu: float | None,
    ) -> float:
        if (
            avg_lsu_per_ha is None
            or effective_stocking_rate_ha_per_lsu is None
            or avg_lsu_per_ha <= 0
            or effective_stocking_rate_ha_per_lsu <= 0
        ):
            return 0.0
        return max(0.0, avg_lsu_per_ha * effective_stocking_rate_ha_per_lsu)

    @staticmethod
    def _interpolate_channel(start: int, end: int, ratio: float) -> int:
        return int(round(start + ((end - start) * ratio)))

    @classmethod
    def _interpolate_color(cls, start_hex: str, end_hex: str, ratio: float) -> str:
        ratio = max(0.0, min(1.0, ratio))
        start = tuple(int(start_hex[idx : idx + 2], 16) for idx in (1, 3, 5))
        end = tuple(int(end_hex[idx : idx + 2], 16) for idx in (1, 3, 5))
        channels = [
            cls._interpolate_channel(start_channel, end_channel, ratio)
            for start_channel, end_channel in zip(start, end)
        ]
        return "#{:02x}{:02x}{:02x}".format(*channels)

    @classmethod
    def timeline_color_for_ratio(cls, ratio: float | None) -> str:
        value = max(0.0, min(2.0, float(ratio or 0.0)))
        if value <= 1.0:
            return cls._interpolate_color(cls.GREEN, cls.BLUE, value)
        return cls._interpolate_color(cls.BLUE, cls.RED, value - 1.0)

    @classmethod
    def gradient_for_ratios(cls, ratios: list[float]) -> str:
        if not ratios:
            return cls.GREEN
        colors = [cls.timeline_color_for_ratio(ratio) for ratio in ratios]
        if len(colors) == 1:
            return colors[0]

        step = 100.0 / len(colors)
        stops = []
        for index, color in enumerate(colors):
            left = round(index * step, 4)
            right = round((index + 1) * step, 4)
            stops.append(f"{color} {left}%")
            stops.append(f"{color} {right}%")
        return f"linear-gradient(90deg, {', '.join(stops)})"

    @classmethod
    def close_open_history_for_session(cls, session: GrazingSession, end_at: datetime) -> None:
        rows = (
            GrazingAllocationLsuHistory.query.filter(
                GrazingAllocationLsuHistory.grazing_session_id == session.id,
                GrazingAllocationLsuHistory.effective_to.is_(None),
            )
            .order_by(GrazingAllocationLsuHistory.effective_from.asc())
            .all()
        )
        for row in rows:
            cls._close_or_delete_open_row(row, end_at)

    @classmethod
    def sync_live_history_for_mob(cls, mob: Mob | str, effective_at: datetime | None = None) -> None:
        mob_obj = mob if isinstance(mob, Mob) else db.session.get(Mob, mob)
        if mob_obj is None:
            return

        sync_at = effective_at or datetime.now(timezone.utc)
        open_rows = (
            GrazingAllocationLsuHistory.query.filter(
                GrazingAllocationLsuHistory.mob_id == mob_obj.id,
                GrazingAllocationLsuHistory.effective_to.is_(None),
            )
            .order_by(GrazingAllocationLsuHistory.effective_from.asc())
            .all()
        )

        if mob_obj.status != "active":
            close_at = mob_obj.updated_at or sync_at
            for row in open_rows:
                cls._close_or_delete_open_row(row, close_at)
            return

        active_session = (
            GrazingSession.query.filter_by(mob_id=mob_obj.id, end_at=None)
            .order_by(GrazingSession.start_at.desc())
            .first()
        )

        if active_session is None:
            for row in open_rows:
                cls._close_or_delete_open_row(row, sync_at)
            return

        mob_total_lsu = ReportingService.mob_total_lsu(active_session.mob)
        allocations = list(active_session.allocations)
        open_rows_by_allocation = {
            str(row.grazing_allocation_id): row
            for row in open_rows
            if str(row.grazing_session_id) == str(active_session.id)
        }
        allocation_ids = {str(allocation.id) for allocation in allocations}

        for row in open_rows:
            if str(row.grazing_session_id) != str(active_session.id):
                cls._close_or_delete_open_row(row, sync_at)
                continue
            if str(row.grazing_allocation_id) not in allocation_ids:
                cls._close_or_delete_open_row(row, sync_at)

        for allocation in allocations:
            allocation_fraction = float(allocation.allocation_fraction)
            allocated_lsu = mob_total_lsu * allocation_fraction
            existing_row = open_rows_by_allocation.get(str(allocation.id))

            if allocated_lsu <= 0:
                if existing_row is not None:
                    cls._close_or_delete_open_row(existing_row, sync_at)
                continue

            if existing_row is not None and cls._history_values_match(
                existing_row,
                allocation_fraction=allocation_fraction,
                mob_total_lsu=mob_total_lsu,
                allocated_lsu=allocated_lsu,
            ):
                continue

            if existing_row is not None:
                existing_from = cls._normalize_datetime(existing_row.effective_from)
                sync_from = cls._normalize_datetime(sync_at)
                if existing_from is not None and sync_from is not None and existing_from == sync_from:
                    existing_row.allocation_fraction = allocation_fraction
                    existing_row.mob_total_lsu = mob_total_lsu
                    existing_row.allocated_lsu = allocated_lsu
                    existing_row.source = cls.SOURCE_LIVE
                    continue
                cls._close_or_delete_open_row(existing_row, sync_at)

            db.session.add(
                GrazingAllocationLsuHistory(
                    farm_id=active_session.farm_id,
                    mob_id=active_session.mob_id,
                    paddock_id=allocation.paddock_id,
                    grazing_session_id=active_session.id,
                    grazing_allocation_id=allocation.id,
                    effective_from=sync_at,
                    allocation_fraction=allocation_fraction,
                    mob_total_lsu=mob_total_lsu,
                    allocated_lsu=allocated_lsu,
                    source=cls.SOURCE_LIVE,
                )
            )

    @classmethod
    def _stock_delta_for_event(cls, event_type: StockEventType, quantity: int) -> int:
        if event_type in cls.STOCK_IN_TYPES:
            return quantity
        if event_type in cls.STOCK_OUT_TYPES:
            return -quantity
        return 0

    @classmethod
    def mob_lsu_intervals_from_ledger(
        cls, mob: Mob | str, *, as_of: datetime | None = None
    ) -> dict:
        mob_id = str(mob.id) if isinstance(mob, Mob) else str(mob)
        max_time = as_of or datetime.now(timezone.utc)
        rows = (
            db.session.query(StockLedgerEntry, AnimalGroupType)
            .join(AnimalGroupType, StockLedgerEntry.animal_group_type_id == AnimalGroupType.id)
            .filter(
                StockLedgerEntry.mob_id == mob_id,
                StockLedgerEntry.event_time <= max_time,
            )
            .order_by(StockLedgerEntry.event_time.asc(), StockLedgerEntry.id.asc())
            .all()
        )

        if not rows:
            return {"mob_id": mob_id, "coverage_start": None, "intervals": []}

        group_meta: dict[str, AnimalGroupType] = {}
        group_counts: dict[str, int] = defaultdict(int)
        intervals = []
        coverage_start = None
        previous_time = None

        for entry, group_type in rows:
            event_time = cls._normalize_datetime(entry.event_time)
            delta = cls._stock_delta_for_event(entry.event_type, int(entry.quantity))
            if delta == 0:
                continue

            if previous_time is not None and previous_time < event_time:
                current_total = cls._mob_total_lsu_from_counts(group_counts, group_meta)
                if current_total > 0:
                    intervals.append(
                        {
                            "start": previous_time,
                            "end": event_time,
                            "mob_total_lsu": current_total,
                        }
                    )

            group_id = str(entry.animal_group_type_id)
            group_meta[group_id] = group_type
            next_count = group_counts.get(group_id, 0) + delta
            group_counts[group_id] = max(0, next_count)
            if group_counts[group_id] == 0:
                group_counts.pop(group_id, None)

            if coverage_start is None:
                coverage_start = event_time
            previous_time = event_time

        if previous_time is not None:
            current_total = cls._mob_total_lsu_from_counts(group_counts, group_meta)
            if current_total > 0:
                intervals.append(
                    {
                        "start": previous_time,
                        "end": None,
                        "mob_total_lsu": current_total,
                    }
                )

        return {"mob_id": mob_id, "coverage_start": coverage_start, "intervals": intervals}

    @classmethod
    def _mob_total_lsu_from_counts(
        cls, group_counts: dict[str, int], group_meta: dict[str, AnimalGroupType]
    ) -> float:
        total = 0.0
        for group_id, head_count in group_counts.items():
            if head_count <= 0:
                continue
            group = group_meta[group_id]
            total += head_count * ReportingService.group_lsu_per_head(
                group.species, group.sex, group.age_class
            )
        return total

    @classmethod
    def backfill_all_from_ledger(cls, *, as_of: datetime | None = None) -> dict:
        rebuild_at = as_of or datetime.now(timezone.utc)
        db.session.query(GrazingAllocationLsuHistory).delete(synchronize_session=False)
        db.session.flush()

        rows_created = 0
        mobs_backfilled = 0
        allocations = (
            GrazingAllocation.query.join(GrazingSession)
            .order_by(
                GrazingSession.mob_id.asc(),
                GrazingSession.start_at.asc(),
                GrazingAllocation.created_at.asc(),
            )
            .all()
        )
        allocations_by_mob: dict[str, list[GrazingAllocation]] = defaultdict(list)
        for allocation in allocations:
            allocations_by_mob[str(allocation.grazing_session.mob_id)].append(allocation)

        for mob in Mob.query.order_by(Mob.name.asc()).all():
            ledger_timeline = cls.mob_lsu_intervals_from_ledger(mob, as_of=rebuild_at)
            intervals = ledger_timeline["intervals"]
            if intervals:
                mobs_backfilled += 1

            for allocation in allocations_by_mob.get(str(mob.id), []):
                session = allocation.grazing_session
                session_start = cls._normalize_datetime(session.start_at)
                session_end = cls._normalize_datetime(session.end_at)
                allocation_fraction = float(allocation.allocation_fraction)

                for interval in intervals:
                    overlap_start = max(session_start, interval["start"])
                    overlap_end = cls._min_datetime(session_end, interval["end"])
                    if overlap_end is not None and overlap_end <= overlap_start:
                        continue

                    allocated_lsu = interval["mob_total_lsu"] * allocation_fraction
                    if allocated_lsu <= 0:
                        continue

                    db.session.add(
                        GrazingAllocationLsuHistory(
                            farm_id=session.farm_id,
                            mob_id=session.mob_id,
                            paddock_id=allocation.paddock_id,
                            grazing_session_id=session.id,
                            grazing_allocation_id=allocation.id,
                            effective_from=overlap_start,
                            effective_to=overlap_end,
                            allocation_fraction=allocation_fraction,
                            mob_total_lsu=interval["mob_total_lsu"],
                            allocated_lsu=allocated_lsu,
                            source=cls.SOURCE_BACKFILL,
                        )
                    )
                    rows_created += 1

        db.session.flush()

        active_sessions = GrazingSession.query.join(Mob).filter(
            GrazingSession.end_at.is_(None),
            Mob.status == "active",
        ).all()
        for session in active_sessions:
            open_rows = (
                GrazingAllocationLsuHistory.query.filter(
                    GrazingAllocationLsuHistory.grazing_session_id == session.id,
                    GrazingAllocationLsuHistory.effective_to.is_(None),
                )
                .limit(1)
                .all()
            )
            if not open_rows:
                cls.sync_live_history_for_mob(session.mob, effective_at=rebuild_at)

        return {"mobs_backfilled": mobs_backfilled, "rows_created": rows_created}

    @staticmethod
    def _min_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
        if left is None:
            return right
        if right is None:
            return left
        return min(left, right)

    @classmethod
    def build_paddock_daily_metrics(
        cls,
        paddocks: list[Paddock],
        *,
        start_date: date,
        end_date: date,
    ) -> dict:
        labels = []
        cursor = start_date
        while cursor <= end_date:
            labels.append(cursor.isoformat())
            cursor += timedelta(days=1)

        if not paddocks:
            return {"labels": labels, "paddocks": {}, "ticks": []}

        paddock_ids = [str(paddock.id) for paddock in paddocks]
        earliest_year_start = datetime.combine(date(start_date.year, 1, 1), time.min)
        end_dt = datetime.combine(end_date + timedelta(days=1), time.min)

        history_rows = (
            GrazingAllocationLsuHistory.query.filter(
                GrazingAllocationLsuHistory.paddock_id.in_(paddock_ids),
                GrazingAllocationLsuHistory.effective_from < end_dt,
                or_(
                    GrazingAllocationLsuHistory.effective_to.is_(None),
                    GrazingAllocationLsuHistory.effective_to > earliest_year_start,
                ),
            )
            .order_by(
                GrazingAllocationLsuHistory.paddock_id.asc(),
                GrazingAllocationLsuHistory.effective_from.asc(),
                GrazingAllocationLsuHistory.id.asc(),
            )
            .all()
        )

        rows_by_paddock: dict[str, list[dict]] = defaultdict(list)
        for row in history_rows:
            rows_by_paddock[str(row.paddock_id)].append(
                {
                    "start": cls._normalize_datetime(row.effective_from),
                    "end": cls._normalize_datetime(row.effective_to),
                    "allocated_lsu": float(row.allocated_lsu),
                    "mob_id": str(row.mob_id),
                    "mob_name": row.mob.name if row.mob is not None else str(row.mob_id),
                }
            )

        session_bounds = (
            db.session.query(
                GrazingAllocation.paddock_id.label("paddock_id"),
                func.min(GrazingSession.start_at).label("earliest_session"),
            )
            .join(GrazingSession, GrazingAllocation.grazing_session_id == GrazingSession.id)
            .filter(GrazingAllocation.paddock_id.in_(paddock_ids))
            .group_by(GrazingAllocation.paddock_id)
            .all()
        )
        earliest_session_by_paddock = {
            str(row.paddock_id): cls._normalize_datetime(row.earliest_session) for row in session_bounds
        }
        earliest_history_rows = (
            db.session.query(
                GrazingAllocationLsuHistory.paddock_id.label("paddock_id"),
                func.min(GrazingAllocationLsuHistory.effective_from).label("earliest_history"),
            )
            .filter(GrazingAllocationLsuHistory.paddock_id.in_(paddock_ids))
            .group_by(GrazingAllocationLsuHistory.paddock_id)
            .all()
        )
        earliest_history_by_paddock = {
            str(row.paddock_id): cls._normalize_datetime(row.earliest_history)
            for row in earliest_history_rows
        }

        tick_rows = []
        total_visible_days = max(1, (end_date - start_date).days + 1)
        tick_cursor = date(start_date.year, start_date.month, 1)
        if tick_cursor < start_date:
            tick_cursor = date(start_date.year + (1 if start_date.month == 12 else 0), 1 if start_date.month == 12 else start_date.month + 1, 1)
        while tick_cursor <= end_date:
            offset_days = (tick_cursor - start_date).days
            tick_rows.append(
                {
                    "label": tick_cursor.strftime("%b %Y"),
                    "left_pct": round((offset_days / total_visible_days) * 100.0, 4),
                }
            )
            if tick_cursor.month == 12:
                tick_cursor = date(tick_cursor.year + 1, 1, 1)
            else:
                tick_cursor = date(tick_cursor.year, tick_cursor.month + 1, 1)

        paddock_payload = {}
        for paddock in paddocks:
            paddock_id = str(paddock.id)
            context = cls.paddock_capacity_context(paddock)
            rows = rows_by_paddock.get(paddock_id, [])
            ytd_used_sdh = 0.0
            current_year = start_date.year
            all_daily = []
            compute_cursor = earliest_year_start.date()

            while compute_cursor <= end_date:
                if compute_cursor.year != current_year:
                    current_year = compute_cursor.year
                    ytd_used_sdh = 0.0

                day_stats = cls._build_day_stats(
                    rows=rows,
                    day=compute_cursor,
                    area_ha=context["area_ha"],
                    effective_stocking_rate=context["effective_stocking_rate_ha_per_lsu"],
                )
                effective_area_ha = context["effective_area_ha"]
                grazing_capacity_sdh = context["grazing_capacity_sdh"]
                if effective_area_ha > 0:
                    day_sdh = day_stats["avg_lsu"] / effective_area_ha
                    ytd_used_sdh += day_sdh
                else:
                    day_sdh = None
                if grazing_capacity_sdh and effective_area_ha > 0:
                    pressure_pct = (ytd_used_sdh / grazing_capacity_sdh) * 100.0
                else:
                    pressure_pct = None
                day_stats["pressure_pct"] = pressure_pct

                if compute_cursor >= start_date:
                    all_daily.append(day_stats)
                compute_cursor += timedelta(days=1)

            paddock_payload[paddock_id] = {
                "daily": all_daily,
                "metrics": {
                    "current_lsu": [row["current_lsu"] for row in all_daily],
                    "lsu_per_ha": [row["lsu_per_ha"] for row in all_daily],
                    "ha_per_lsu": [row["ha_per_lsu"] for row in all_daily],
                    "pressure_pct": [row["pressure_pct"] for row in all_daily],
                },
                "periods": cls._build_periods(
                    paddock=paddock,
                    daily_rows=all_daily,
                ),
                "coverage": {
                    "earliest_session": earliest_session_by_paddock.get(paddock_id),
                    "earliest_history": earliest_history_by_paddock.get(paddock_id),
                    "has_uncovered_legacy": cls._has_uncovered_legacy(
                        earliest_session=earliest_session_by_paddock.get(paddock_id),
                        earliest_history=earliest_history_by_paddock.get(paddock_id),
                    ),
                },
            }

        return {"labels": labels, "paddocks": paddock_payload, "ticks": tick_rows}

    @classmethod
    def _build_day_stats(
        cls,
        *,
        rows: list[dict],
        day: date,
        area_ha: float,
        effective_stocking_rate: float,
    ) -> dict:
        day_start = datetime.combine(day, time.min)
        day_end = day_start + timedelta(days=1)
        current_lsu = 0.0
        peak_lsu = 0.0
        avg_lsu = 0.0
        unique_mob_names = set()
        change_points = set()
        event_deltas: dict[datetime, float] = defaultdict(float)

        for row in rows:
            row_start = row["start"]
            row_end = row["end"] or day_end
            if row_start < day_start and row_end > day_start:
                current_lsu += row["allocated_lsu"]
            overlap_start = max(row_start, day_start)
            overlap_end = min(row_end, day_end)
            if overlap_end <= overlap_start:
                continue

            duration_days = (overlap_end - overlap_start).total_seconds() / 86400.0
            avg_lsu += row["allocated_lsu"] * duration_days
            unique_mob_names.add(row["mob_name"])

            if row_start >= day_start and row_start < day_end:
                event_deltas[row_start] += row["allocated_lsu"]
                change_points.add(row_start.isoformat())
            if row["end"] is not None and row_end > day_start and row_end < day_end:
                event_deltas[row_end] -= row["allocated_lsu"]
                change_points.add(row_end.isoformat())

        peak_lsu = current_lsu
        for point in sorted(event_deltas):
            current_lsu += event_deltas[point]
            peak_lsu = max(peak_lsu, current_lsu)

        end_of_day_lsu = current_lsu
        lsu_per_ha = (end_of_day_lsu / area_ha) if area_ha > 0 else None
        ha_per_lsu = (area_ha / end_of_day_lsu) if end_of_day_lsu > 0 and area_ha > 0 else None
        avg_lsu_per_ha = (avg_lsu / area_ha) if area_ha > 0 else None
        avg_ha_per_lsu = (area_ha / avg_lsu) if avg_lsu > 0 and area_ha > 0 else None
        intensity_ratio = cls.timeline_intensity_ratio(
            avg_lsu_per_ha=avg_lsu_per_ha,
            effective_stocking_rate_ha_per_lsu=effective_stocking_rate,
        )

        return {
            "date": day.isoformat(),
            "state": "grazed" if avg_lsu > 0 else "rested",
            "current_lsu": round(end_of_day_lsu, 4),
            "avg_lsu": round(avg_lsu, 4),
            "peak_lsu": round(peak_lsu, 4),
            "lsu_per_ha": round(lsu_per_ha, 4) if lsu_per_ha is not None else None,
            "ha_per_lsu": round(ha_per_lsu, 4) if ha_per_lsu is not None else None,
            "avg_lsu_per_ha": round(avg_lsu_per_ha, 4) if avg_lsu_per_ha is not None else None,
            "avg_ha_per_lsu": round(avg_ha_per_lsu, 4) if avg_ha_per_lsu is not None else None,
            "intensity_ratio": round(intensity_ratio, 4),
            "intensity_color": cls.timeline_color_for_ratio(intensity_ratio),
            "mob_names": sorted(unique_mob_names),
            "change_points": sorted(change_points),
        }

    @classmethod
    def _build_periods(cls, *, paddock: Paddock, daily_rows: list[dict]) -> list[dict]:
        if not daily_rows:
            return []

        periods = []
        total_days = len(daily_rows)
        current_days = [daily_rows[0]]

        for row in daily_rows[1:]:
            previous_day = date.fromisoformat(current_days[-1]["date"])
            current_day = date.fromisoformat(row["date"])
            if row["state"] == current_days[-1]["state"] and current_day == previous_day + timedelta(days=1):
                current_days.append(row)
                continue
            periods.append(
                cls._build_period_payload(
                    paddock=paddock,
                    days=current_days,
                    start_index=len(periods) and sum(len(period["days"]) for period in periods) or 0,
                    total_days=total_days,
                )
            )
            current_days = [row]

        periods.append(
            cls._build_period_payload(
                paddock=paddock,
                days=current_days,
                start_index=sum(len(period["days"]) for period in periods),
                total_days=total_days,
            )
        )
        for period in periods:
            period.pop("days", None)
        return periods

    @classmethod
    def _build_period_payload(
        cls,
        *,
        paddock: Paddock,
        days: list[dict],
        start_index: int,
        total_days: int,
    ) -> dict:
        duration_days = len(days)
        end_index = start_index + duration_days - 1
        avg_lsu_values = [row["avg_lsu"] for row in days]
        avg_lsu = sum(avg_lsu_values) / duration_days if duration_days else 0.0
        avg_ha_values = [row["avg_ha_per_lsu"] for row in days if row["avg_ha_per_lsu"] is not None]
        unique_mobs = sorted({name for row in days for name in row["mob_names"]})
        change_points = sorted({point for row in days for point in row["change_points"]})
        intensity_ratios = [row["intensity_ratio"] for row in days]
        is_rested = days[0]["state"] == "rested"
        background_style = cls.GREEN if is_rested else cls.gradient_for_ratios(intensity_ratios)
        details = {
            "paddock_name": paddock.name,
            "state": "Rested" if is_rested else "Grazed",
            "start_date": days[0]["date"],
            "end_date": days[-1]["date"],
            "duration_days": duration_days,
            "average_lsu": round(avg_lsu, 2),
            "peak_lsu": round(max(row["peak_lsu"] for row in days), 2),
            "average_ha_per_lsu": round((float(paddock.area_ha or 0) / avg_lsu), 2)
            if avg_lsu > 0 and float(paddock.area_ha or 0) > 0
            else None,
            "tightest_ha_per_lsu": round(min(avg_ha_values), 2) if avg_ha_values else None,
            "pressure_start_pct": round(days[0]["pressure_pct"], 2)
            if days[0]["pressure_pct"] is not None
            else None,
            "pressure_end_pct": round(days[-1]["pressure_pct"], 2)
            if days[-1]["pressure_pct"] is not None
            else None,
            "unique_mobs": unique_mobs,
            "change_points_count": len(change_points),
        }
        return {
            "id": f"{paddock.id}:{days[0]['date']}:{days[-1]['date']}",
            "paddock_id": str(paddock.id),
            "state": days[0]["state"],
            "start_date": days[0]["date"],
            "end_date": days[-1]["date"],
            "left_pct": round((start_index / max(total_days, 1)) * 100.0, 4),
            "width_pct": round((duration_days / max(total_days, 1)) * 100.0, 4),
            "background_style": background_style,
            "details": details,
            "days": list(days),
        }

    @staticmethod
    def _has_uncovered_legacy(
        *,
        earliest_session: datetime | None,
        earliest_history: datetime | None,
    ) -> bool:
        if earliest_session is None:
            return False
        if earliest_history is None:
            return True
        return earliest_session < earliest_history
