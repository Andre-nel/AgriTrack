"""Read-only farm inventory reconstruction; dates represent UTC closing balances.

The ledger describes quantities, while state history describes ownership. Keeping
these separate avoids counting grazing allocations or using a posting's farm as
the destination of a later whole-mob movement.
"""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select, union
from sqlalchemy.orm import selectinload

from app.models import (
    AnimalGroupType, FarmCapacityHistory, GrazingSession, Mob,
    MobInventoryStateHistory, MovementEvent, MovementEventMob, StockLedgerEntry,
)
from app.services.reporting_service import ReportingService
from app.models.stock_ledger import STOCK_IN_EVENTS


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def signed_quantity(entry):
    """Legacy absolute counts have no trustworthy signed interpretation."""
    if entry.event_type.value == "count":
        return None
    return int(entry.quantity) * (1 if entry.event_type in STOCK_IN_EVENTS else -1)


def _state(row):
    return (str(row.farm_id) if row.farm_id else None, bool(row.active))


def _previous_state(row):
    return (str(row.previous_farm_id) if row.previous_farm_id else None, row.previous_active)


def _lookup(points, times, at):
    index = bisect_right(times, at) - 1
    return points[index][1] if index >= 0 else None


def _legacy_states(baseline, entries, sessions, movements, residual):
    """Accept legacy ownership only when every assertion fits one movement chain.

    A posting anchors ownership but does not move an existing mob. Session starts
    and whole-mob movement events can move it; merge-source records end activity.
    Conflicting evidence invalidates this legacy reconstruction, never the saved
    baseline. No state is inferred before the earliest evidence.
    """
    if any(residual.values()):
        return []
    cutoff = utc(baseline.effective_at)
    evidence = defaultdict(lambda: {"assertions": set(), "destinations": set(), "archive": False})
    for row in entries:
        at = utc(row.event_time)
        # A late backdated posting stores the farm registered when posted, which
        # need not be the farm that owned the mob on its effective date. It changes
        # quantity along a proven chain, never rewrites that chain's ownership.
        if at < cutoff and utc(row.created_at) <= utc(baseline.recorded_at):
            evidence[at]["assertions"].add(str(row.farm_id))
    for row in sessions:
        at = utc(row.start_at)
        if at < cutoff:
            evidence[at]["destinations"].add(str(row.farm_id))
    for event, role in movements:
        at = utc(event.event_time)
        if at >= cutoff:
            continue
        if event.event_kind.value == "move":
            evidence[at]["destinations"].add(str(event.farm_id))
        elif event.event_kind.value == "merge" and role.value == "source":
            evidence[at]["archive"] = True
        else:
            evidence[at]["assertions"].add(str(event.farm_id))
    state = None
    points = []
    for at, item in sorted(evidence.items()):
        if len(item["destinations"]) > 1 or len(item["assertions"]) > 1:
            return []
        destination = next(iter(item["destinations"]), None)
        asserted = next(iter(item["assertions"]), None)
        if destination:
            state = (destination, True)
        if state is None and asserted:
            state = (asserted, True)
        if state is None or (asserted and asserted != state[0]):
            return []
        if item["archive"]:
            state = (state[0], False)
        points.append((at, state))
    if state != _state(baseline):
        return []
    return points


@dataclass
class MobInventorySeries:
    values: list[tuple[str | None, Decimal | None, set[str]]]
    observation_start: dict[str, datetime]


def mob_daily_inventory(mob, states, entries, sessions, movements, factors, samples):
    """Pure reconstruction returning (farm, LSU, possible unknown farms) per sample."""
    states = sorted(states, key=lambda row: (utc(row.effective_at), row.id))
    baselines = [row for row in states if row.source in {"baseline", "created"}]
    possible_farms = {str(mob.farm_id)}
    for row in states:
        possible_farms.update(str(value) for value in (row.farm_id, row.previous_farm_id) if value)
    possible_farms.update(str(row.farm_id) for row in entries)
    possible_farms.update(str(row.farm_id) for row in sessions)
    possible_farms.update(str(event.farm_id) for event, _role in movements)
    if not baselines:
        return MobInventorySeries([(None, None, possible_farms) for _at in samples], {})
    baseline = baselines[0]
    baseline_at = utc(baseline.effective_at)
    residual = defaultdict(int)
    for group in baseline.baseline_groups:
        residual[str(group.animal_group_type_id)] += int(group.head_count) - int(group.ledger_net)

    # Batch all postings at a timestamp before checking nonnegative counts. Cohort
    # transfers and paired reclassification must never create intermediate stock.
    batches = defaultdict(lambda: defaultdict(int))
    unsupported_at = None
    unsupported_legacy = False
    for entry in entries:
        at = utc(entry.event_time)
        delta = signed_quantity(entry)
        if delta is None:
            unsupported_legacy = True
            # An old absolute count is absorbed by the captured head count. New
            # absolute counts cannot be interpreted without rewriting the ledger.
            if at >= baseline_at or utc(entry.created_at) > utc(baseline.recorded_at):
                unsupported_at = min(unsupported_at, at) if unsupported_at else at
        else:
            batches[at][str(entry.animal_group_type_id)] += delta
    counts = dict(residual)
    stock_points = []
    legacy_counts_valid = not any(residual.values())
    for at, deltas in sorted(batches.items()):
        for group_id, delta in deltas.items():
            counts[group_id] = counts.get(group_id, 0) + delta
        valid = all(value >= 0 for value in counts.values()) and all(
            group_id in factors for group_id, value in counts.items() if value
        )
        if at < baseline_at and not valid:
            legacy_counts_valid = False
        value = sum((Decimal(count) * factors[group_id] for group_id, count in counts.items()
                     if count and group_id in factors), Decimal(0)) if valid else None
        stock_points.append((at, value))
    initial_valid = all(value >= 0 and (not value or key in factors) for key, value in residual.items())
    initial_lsu = sum((Decimal(count) * factors[key] for key, count in residual.items()
                       if count and key in factors), Decimal(0)) if initial_valid else None
    legacy_states = _legacy_states(baseline, entries, sessions, movements, residual) if legacy_counts_valid and not unsupported_legacy else []

    # New rows contain both ends of a transition. Replay in effective order and
    # reject discontinuity caused by contradictory backdated movements.
    changes = [row for row in states if row.source not in {"baseline", "created"}]
    current = _state(baseline)
    state_points = [(baseline_at, current)]
    conflict_at = None
    for row in reversed([row for row in changes if utc(row.effective_at) < baseline_at]):
        if _state(row) != current:
            conflict_at = utc(row.effective_at)
        current = _previous_state(row)
    current = _state(baseline)
    for row in changes:
        at = utc(row.effective_at)
        if at < baseline_at:
            continue
        if _previous_state(row) != current:
            conflict_at = min(conflict_at, at) if conflict_at else at
        current = _state(row)
        state_points.append((at, current))

    # Live counts include future postings too. A reconciliation mismatch indicates
    # a write bypassed the ledger/state recorder; its effective date is unknown.
    if hasattr(mob, "balances"):
        live_counts = defaultdict(int)
        for balance in mob.balances:
            live_counts[str(balance.animal_group_type_id)] += int(balance.head_count)
        if any(counts.get(key, 0) != live_counts.get(key, 0) for key in set(counts) | set(live_counts)):
            unsupported_at = min(unsupported_at, baseline_at) if unsupported_at else baseline_at
    if hasattr(mob, "status") and current != (str(mob.farm_id), mob.status == "active"):
        conflict_at = min(conflict_at, baseline_at) if conflict_at else baseline_at

    results = []
    stock_times = [point[0] for point in stock_points]
    legacy_times = [point[0] for point in legacy_states]
    state_times = [point[0] for point in state_points]
    prior_evidence = [utc(row.event_time) for row in entries] + [utc(row.start_at) for row in sessions]
    prior_evidence += [utc(event.event_time) for event, _role in movements]
    first_evidence = min([baseline_at, *prior_evidence])
    for at in samples:
        if conflict_at and at >= conflict_at:
            results.append((None, None, possible_farms))
            continue
        if at < baseline_at:
            if baseline.source == "created" and at < first_evidence:
                results.append((None, Decimal(0), set()))
                continue
            state = _lookup(legacy_states, legacy_times, at)
        else:
            state = _lookup(state_points, state_times, at)
        if state is None:
            results.append((None, None, possible_farms))
            continue
        farm_id, active = state
        if not active:
            results.append((farm_id, Decimal(0), set()))
            continue
        position = bisect_right(stock_times, at) - 1
        value = stock_points[position][1] if position >= 0 else initial_lsu
        if unsupported_at and at >= unsupported_at:
            value = None
        results.append((farm_id, value, {farm_id} if value is None else set()))
    observed = {}
    for at, (farm_id, _active) in [*legacy_states, *state_points]:
        if farm_id and (not conflict_at or at < conflict_at):
            observed[farm_id] = min(observed.get(farm_id, at), at)
    return MobInventorySeries(results, observed)


def build_farm_lsu_history(farms, *, start_date, end_date, now=None):
    """Load once per source, then calculate all requested dates in memory."""
    now = utc(now or datetime.now(timezone.utc))
    dates = [start_date + timedelta(days=index) for index in range((end_date - start_date).days + 1)]
    samples = [min(datetime.combine(day, time.max, timezone.utc), now) for day in dates]
    farm_ids = {str(farm.id) for farm in farms}
    payload = {str(farm.id): {"name": farm.name, "capacity": [], "livestock": [Decimal(0)] * len(dates),
                             "current_livestock": Decimal(0)} for farm in farms}
    if not farms:
        return {"labels": [day.isoformat() for day in dates], "farms": payload}

    capacity_by_farm = defaultdict(list)
    for row in FarmCapacityHistory.query.filter(FarmCapacityHistory.farm_id.in_(farm_ids)).order_by(
        FarmCapacityHistory.effective_at, FarmCapacityHistory.id
    ).all():
        capacity_by_farm[str(row.farm_id)].append((utc(row.effective_at), row.capacity_lsu))
    for farm_id in farm_ids:
        points = capacity_by_farm[farm_id]
        times = [point[0] for point in points]
        payload[farm_id]["capacity"] = [_lookup(points, times, at) for at in samples]
    observation_start = {farm_id: points[0][0] for farm_id, points in capacity_by_farm.items() if points}

    # A mob may now belong to a different farm. Load historical state/stock across
    # farms rather than filtering mobs by their current farm and losing transfers.
    candidate_ids = union(
        select(Mob.id).where(Mob.farm_id.in_(farm_ids)),
        select(MobInventoryStateHistory.mob_id).where(or_(
            MobInventoryStateHistory.farm_id.in_(farm_ids),
            MobInventoryStateHistory.previous_farm_id.in_(farm_ids))),
        select(StockLedgerEntry.mob_id).where(StockLedgerEntry.farm_id.in_(farm_ids)),
        select(GrazingSession.mob_id).where(GrazingSession.farm_id.in_(farm_ids)),
        select(MovementEventMob.mob_id).join(MovementEvent).where(MovementEvent.farm_id.in_(farm_ids)),
    )
    mobs = Mob.query.filter(Mob.id.in_(candidate_ids)).options(selectinload(Mob.balances)).all()
    mob_ids = [str(mob.id) for mob in mobs]
    factors = {str(group.id): Decimal(str(ReportingService.group_lsu_per_head(
        group.species, group.sex, group.age_class))) for group in AnimalGroupType.query.all()}
    states_by_mob, entries_by_mob, sessions_by_mob, moves_by_mob = (defaultdict(list) for _ in range(4))
    for row in MobInventoryStateHistory.query.filter(MobInventoryStateHistory.mob_id.in_(mob_ids)).options(
        selectinload(MobInventoryStateHistory.baseline_groups)
    ).all():
        states_by_mob[str(row.mob_id)].append(row)
    for row in StockLedgerEntry.query.filter(StockLedgerEntry.mob_id.in_(mob_ids)).all():
        entries_by_mob[str(row.mob_id)].append(row)
    for row in GrazingSession.query.filter(GrazingSession.mob_id.in_(mob_ids)).all():
        sessions_by_mob[str(row.mob_id)].append(row)
    for event, link in MovementEvent.query.join(MovementEventMob).filter(
        MovementEventMob.mob_id.in_(mob_ids)
    ).add_entity(MovementEventMob).all():
        moves_by_mob[str(link.mob_id)].append((event, link.role))
    for mob in mobs:
        mob_id = str(mob.id)
        if mob.status == "active" and str(mob.farm_id) in farm_ids:
            payload[str(mob.farm_id)]["current_livestock"] += sum(
                (Decimal(row.head_count) * factors[str(row.animal_group_type_id)] for row in mob.balances), Decimal(0)
            )
        series = mob_daily_inventory(mob, states_by_mob[mob_id], entries_by_mob[mob_id],
                                   sessions_by_mob[mob_id], moves_by_mob[mob_id], factors, samples)
        for farm_id, at in series.observation_start.items():
            observation_start[farm_id] = min(observation_start.get(farm_id, at), at)
        for index, (farm_id, value, unknown_farms) in enumerate(series.values):
            for unknown_id in unknown_farms & farm_ids:
                payload[unknown_id]["livestock"][index] = None
            if farm_id in farm_ids and value is not None and payload[farm_id]["livestock"][index] is not None:
                payload[farm_id]["livestock"][index] += value
    for farm_id, farm_payload in payload.items():
        observed_at = observation_start.get(farm_id)
        for index, at in enumerate(samples):
            if observed_at is None or at < observed_at:
                farm_payload["livestock"][index] = None
    return {"labels": [day.isoformat() for day in dates], "farms": payload}
