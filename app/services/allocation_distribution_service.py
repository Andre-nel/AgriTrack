from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    AnimalGroupType,
    GrazingAllocation,
    GrazingAllocationGroupAssignment,
    GrazingSession,
    Mob,
)
from app.services.reporting_service import ReportingService


FRACTION_QUANT = Decimal("0.0001")
GROUP_FRACTION_QUANT = Decimal("0.000001")


class AllocationDistributionService:
    @staticmethod
    def _mob_obj(mob: Mob | str) -> Mob | None:
        return mob if isinstance(mob, Mob) else db.session.get(Mob, mob)

    @staticmethod
    def _group_lsu_per_head(group: AnimalGroupType) -> Decimal:
        return Decimal(
            str(ReportingService.group_lsu_per_head(group.species, group.sex, group.age_class))
        )

    @classmethod
    def balance_context(
        cls,
        mob: Mob | str,
        *,
        overrides: dict[str, int] | None = None,
    ) -> dict[str, dict]:
        mob_obj = cls._mob_obj(mob)
        if mob_obj is None:
            return {}

        context: dict[str, dict] = {}
        for balance in mob_obj.balances:
            head_count = int(balance.head_count or 0)
            if head_count <= 0:
                continue
            group = balance.animal_group_type
            group_id = str(balance.animal_group_type_id)
            if group_id in context:
                context[group_id]["head_count"] += head_count
            else:
                context[group_id] = {
                    "head_count": head_count,
                    "group": group,
                    "lsu_per_head": cls._group_lsu_per_head(group),
                }

        for group_id, head_count in (overrides or {}).items():
            group_key = str(group_id)
            next_count = int(head_count or 0)
            if next_count <= 0:
                context.pop(group_key, None)
                continue
            group = context.get(group_key, {}).get("group") or db.session.get(AnimalGroupType, group_key)
            if group is None:
                continue
            context[group_key] = {
                "head_count": next_count,
                "group": group,
                "lsu_per_head": cls._group_lsu_per_head(group),
            }
        return context

    @staticmethod
    def active_session_for_mob(mob: Mob | str) -> GrazingSession | None:
        mob_id = str(mob.id) if isinstance(mob, Mob) else str(mob)
        return (
            GrazingSession.query.filter_by(mob_id=mob_id, end_at=None)
            .order_by(GrazingSession.start_at.desc())
            .first()
        )

    @staticmethod
    def _sorted_allocations(session: GrazingSession) -> list[GrazingAllocation]:
        return sorted(
            list(session.allocations),
            key=lambda row: (
                (row.paddock.name.lower() if row.paddock else ""),
                str(row.paddock_id),
            ),
        )

    @staticmethod
    def split_count(total_count: int, weighted_items: list[tuple[str, Decimal | float | int]]) -> dict[str, int]:
        total = int(total_count or 0)
        keys = [str(key) for key, _weight in weighted_items]
        if total <= 0 or not keys:
            return {key: 0 for key in keys}

        weights = [Decimal(str(weight)) for _key, weight in weighted_items]
        if sum((weight for weight in weights if weight > 0), Decimal("0")) <= 0:
            weights = [Decimal("1") for _key in keys]

        positive_total = sum((weight for weight in weights if weight > 0), Decimal("0"))
        base_counts: list[int] = []
        remainders: list[tuple[Decimal, str, int]] = []
        assigned = 0
        for index, (key, weight) in enumerate(zip(keys, weights)):
            if weight <= 0 or positive_total <= 0:
                base_counts.append(0)
                remainders.append((Decimal("0"), key, index))
                continue
            raw = Decimal(total) * (weight / positive_total)
            count = int(raw.to_integral_value(rounding=ROUND_DOWN))
            base_counts.append(count)
            assigned += count
            remainders.append((raw - Decimal(count), key, index))

        remaining = total - assigned
        for _remainder, _key, index in sorted(remainders, key=lambda item: (-item[0], item[1])):
            if remaining <= 0:
                break
            if weights[index] <= 0:
                continue
            base_counts[index] += 1
            remaining -= 1

        return {key: count for key, count in zip(keys, base_counts)}

    @classmethod
    def _distribution_for_group(
        cls,
        target_total: int,
        existing_counts: dict[str, int],
        fallback_weights: list[tuple[str, Decimal | float | int]],
    ) -> dict[str, int]:
        target = int(target_total or 0)
        clean_existing = {
            str(paddock_id): int(count)
            for paddock_id, count in existing_counts.items()
            if int(count or 0) > 0
        }
        current_total = sum(clean_existing.values())
        if target <= 0:
            return {}
        if current_total <= 0:
            return {
                paddock_id: count
                for paddock_id, count in cls.split_count(target, fallback_weights).items()
                if count > 0
            }
        if current_total == target:
            return clean_existing
        if current_total > target:
            removals = cls.split_count(
                current_total - target,
                [(paddock_id, count) for paddock_id, count in clean_existing.items()],
            )
            return {
                paddock_id: count - removals.get(paddock_id, 0)
                for paddock_id, count in clean_existing.items()
                if count - removals.get(paddock_id, 0) > 0
            }

        additions = cls.split_count(
            target - current_total,
            [(paddock_id, count) for paddock_id, count in clean_existing.items()],
        )
        return {
            paddock_id: count + additions.get(paddock_id, 0)
            for paddock_id, count in clean_existing.items()
            if count + additions.get(paddock_id, 0) > 0
        }

    @classmethod
    def snapshot_session_counts(
        cls,
        session: GrazingSession,
        *,
        context: dict[str, dict] | None = None,
    ) -> dict[str, dict[str, int]]:
        context = context if context is not None else cls.balance_context(session.mob)
        allocations = cls._sorted_allocations(session)
        if not allocations or not context:
            return {}

        fallback_weights = [
            (str(allocation.paddock_id), Decimal(str(allocation.allocation_fraction or 0)))
            for allocation in allocations
        ]
        existing_by_group: dict[str, dict[str, int]] = defaultdict(dict)
        for allocation in allocations:
            paddock_id = str(allocation.paddock_id)
            for assignment in allocation.group_assignments:
                head_count = int(assignment.head_count or 0)
                group_id = str(assignment.animal_group_type_id)
                if head_count <= 0 or group_id not in context:
                    continue
                group_counts = existing_by_group[group_id]
                group_counts[paddock_id] = group_counts.get(paddock_id, 0) + head_count

        counts_by_paddock: dict[str, dict[str, int]] = defaultdict(dict)
        for group_id, meta in context.items():
            group_distribution = cls._distribution_for_group(
                int(meta["head_count"]),
                existing_by_group.get(group_id, {}),
                fallback_weights,
            )
            for paddock_id, head_count in group_distribution.items():
                if head_count > 0:
                    counts_by_paddock[paddock_id][group_id] = head_count
        return {
            paddock_id: dict(group_counts)
            for paddock_id, group_counts in counts_by_paddock.items()
            if any(count > 0 for count in group_counts.values())
        }

    @classmethod
    def count_map_for_fraction_allocations(
        cls,
        mob: Mob,
        allocations: list[dict],
        *,
        context: dict[str, dict] | None = None,
    ) -> dict[str, dict[str, int]]:
        context = context if context is not None else cls.balance_context(mob)
        fallback_weights = [
            (
                str(item.get("paddock_id") or "").strip(),
                Decimal(str(item.get("allocation_fraction", 0))),
            )
            for item in allocations
        ]
        fallback_weights = [(paddock_id, weight) for paddock_id, weight in fallback_weights if paddock_id]
        counts_by_paddock: dict[str, dict[str, int]] = defaultdict(dict)
        for group_id, meta in context.items():
            distribution = cls.split_count(int(meta["head_count"]), fallback_weights)
            for paddock_id, count in distribution.items():
                if count > 0:
                    counts_by_paddock[paddock_id][group_id] = count
        return {
            paddock_id: dict(group_counts)
            for paddock_id, group_counts in counts_by_paddock.items()
            if any(count > 0 for count in group_counts.values())
        }

    @classmethod
    def allocations_from_count_map(
        cls,
        mob: Mob,
        counts_by_paddock: dict[str, dict[str, int]],
        *,
        context: dict[str, dict] | None = None,
    ) -> list[dict]:
        context = context if context is not None else cls.balance_context(mob)
        clean_counts: dict[str, dict[str, int]] = {}
        assigned_lsu_by_paddock: dict[str, Decimal] = {}
        for paddock_id, group_counts in counts_by_paddock.items():
            clean_group_counts = {}
            assigned_lsu = Decimal("0")
            for group_id, count in group_counts.items():
                group_key = str(group_id)
                head_count = int(count or 0)
                if head_count <= 0 or group_key not in context:
                    continue
                meta = context[group_key]
                clean_group_counts[group_key] = clean_group_counts.get(group_key, 0) + head_count
                assigned_lsu += Decimal(head_count) * Decimal(str(meta["lsu_per_head"]))
            if clean_group_counts and assigned_lsu > 0:
                clean_counts[str(paddock_id)] = clean_group_counts
                assigned_lsu_by_paddock[str(paddock_id)] = assigned_lsu

        total_lsu = sum(assigned_lsu_by_paddock.values(), Decimal("0"))
        if total_lsu <= 0:
            return []

        allocation_fractions = {
            paddock_id: (assigned_lsu / total_lsu).quantize(
                FRACTION_QUANT,
                rounding=ROUND_HALF_UP,
            )
            for paddock_id, assigned_lsu in assigned_lsu_by_paddock.items()
        }
        delta = Decimal("1") - sum(allocation_fractions.values(), Decimal("0"))
        if delta:
            largest_key = max(
                assigned_lsu_by_paddock,
                key=lambda key: (assigned_lsu_by_paddock[key], key),
            )
            allocation_fractions[largest_key] += delta
            if allocation_fractions[largest_key] <= 0:
                raise ValueError("Count allocations cannot be converted to valid percentages")

        rows = []
        for paddock_id in sorted(clean_counts):
            group_rows = []
            for group_id, head_count in sorted(clean_counts[paddock_id].items()):
                meta = context[group_id]
                group_total = int(meta["head_count"])
                if group_total <= 0:
                    continue
                assigned_lsu = Decimal(head_count) * Decimal(str(meta["lsu_per_head"]))
                group_rows.append(
                    {
                        "animal_group_type_id": group_id,
                        "head_count": head_count,
                        "group_fraction": str(
                            (Decimal(head_count) / Decimal(group_total)).quantize(
                                GROUP_FRACTION_QUANT,
                                rounding=ROUND_HALF_UP,
                            )
                        ),
                        "assigned_lsu": str(assigned_lsu),
                    }
                )
            if group_rows:
                rows.append(
                    {
                        "paddock_id": paddock_id,
                        "allocation_fraction": str(allocation_fractions[paddock_id]),
                        "group_counts": group_rows,
                    }
                )
        return rows

    @classmethod
    def rewrite_session_assignments(
        cls,
        session: GrazingSession,
        counts_by_paddock: dict[str, dict[str, int]],
        *,
        context: dict[str, dict] | None = None,
    ) -> None:
        context = context if context is not None else cls.balance_context(session.mob)
        payloads = cls.allocations_from_count_map(session.mob, counts_by_paddock, context=context)
        payload_by_paddock = {str(row["paddock_id"]): row for row in payloads}
        allocations_by_paddock = {str(row.paddock_id): row for row in session.allocations}

        for payload in payloads:
            paddock_id = str(payload["paddock_id"])
            allocation = allocations_by_paddock.get(paddock_id)
            if allocation is None:
                allocation = GrazingAllocation(
                    grazing_session=session,
                    paddock_id=paddock_id,
                    allocation_fraction=payload["allocation_fraction"],
                )
                db.session.add(allocation)
                db.session.flush()
                allocations_by_paddock[paddock_id] = allocation
            allocation.allocation_fraction = Decimal(str(payload["allocation_fraction"]))

        active_allocations: list[tuple[dict, GrazingAllocation]] = []
        for paddock_id, allocation in list(allocations_by_paddock.items()):
            payload = payload_by_paddock.get(paddock_id)
            allocation.group_assignments.clear()
            if not payload:
                db.session.delete(allocation)
                continue
            active_allocations.append((payload, allocation))
        db.session.flush()

        for payload, allocation in active_allocations:
            for group_count in payload["group_counts"]:
                allocation.group_assignments.append(
                    GrazingAllocationGroupAssignment(
                        animal_group_type_id=group_count["animal_group_type_id"],
                        head_count=int(group_count["head_count"]),
                        group_fraction=Decimal(str(group_count["group_fraction"])),
                        assigned_lsu=Decimal(str(group_count["assigned_lsu"])),
                    )
                )
        db.session.flush()

    @classmethod
    def apply_stock_delta(
        cls,
        mob: Mob | str,
        *,
        animal_group_type_id: str,
        delta: int,
        final_head_count: int,
        paddock_id: str | None = None,
    ) -> None:
        session = cls.active_session_for_mob(mob)
        if session is None:
            return
        group_id = str(animal_group_type_id)
        selected_paddock_id = str(paddock_id or "").strip()
        old_context = cls.balance_context(session.mob)
        counts_by_paddock = cls.snapshot_session_counts(session, context=old_context)
        group_distribution = {
            paddock_id: group_counts.get(group_id, 0)
            for paddock_id, group_counts in counts_by_paddock.items()
            if group_counts.get(group_id, 0) > 0
        }

        if selected_paddock_id:
            assigned_paddock_ids = {
                str(allocation.paddock_id) for allocation in session.allocations
            }
            if selected_paddock_id not in assigned_paddock_ids:
                raise ValueError("Selected paddock is not currently assigned to this mob")

        if delta < 0:
            if selected_paddock_id:
                available = counts_by_paddock.get(selected_paddock_id, {}).get(group_id, 0)
                requested = abs(int(delta))
                if requested > available:
                    raise ValueError(
                        "Selected paddock does not have enough of this animal type "
                        f"for the adjustment ({available} available, {requested} requested)"
                    )
                removals = {selected_paddock_id: requested}
            else:
                removals = cls.split_count(
                    abs(int(delta)),
                    [(paddock_id, count) for paddock_id, count in group_distribution.items()],
                )
            for paddock_id, removal in removals.items():
                if removal <= 0:
                    continue
                next_count = counts_by_paddock.get(paddock_id, {}).get(group_id, 0) - removal
                if next_count > 0:
                    counts_by_paddock[paddock_id][group_id] = next_count
                else:
                    counts_by_paddock.get(paddock_id, {}).pop(group_id, None)
        elif delta > 0:
            if selected_paddock_id:
                additions = {selected_paddock_id: int(delta)}
            else:
                weights = (
                    [(paddock_id, count) for paddock_id, count in group_distribution.items()]
                    if group_distribution
                    else cls._session_split_weights(session, counts_by_paddock, old_context)
                )
                additions = cls.split_count(int(delta), weights)
            for paddock_id, addition in additions.items():
                if addition <= 0:
                    continue
                paddock_counts = counts_by_paddock.setdefault(paddock_id, {})
                paddock_counts[group_id] = paddock_counts.get(group_id, 0) + addition

        final_context = cls.balance_context(
            session.mob,
            overrides={group_id: int(final_head_count or 0)},
        )
        cls.rewrite_session_assignments(session, counts_by_paddock, context=final_context)

    @classmethod
    def apply_reclassification(
        cls,
        mob: Mob,
        *,
        source_group_id: str,
        target_group_id: str,
        target_head_count: int,
    ) -> None:
        session = cls.active_session_for_mob(mob)
        if session is None:
            return

        source_id = str(source_group_id)
        target_id = str(target_group_id)
        old_context = cls.balance_context(mob)
        counts_by_paddock = cls.snapshot_session_counts(session, context=old_context)
        source_distribution = {
            paddock_id: group_counts.get(source_id, 0)
            for paddock_id, group_counts in counts_by_paddock.items()
            if group_counts.get(source_id, 0) > 0
        }
        for group_counts in counts_by_paddock.values():
            group_counts.pop(source_id, None)

        weights = (
            [(paddock_id, count) for paddock_id, count in source_distribution.items()]
            if source_distribution
            else cls._session_split_weights(session, counts_by_paddock, old_context)
        )
        additions = cls.split_count(int(target_head_count or 0), weights)
        for paddock_id, addition in additions.items():
            if addition <= 0:
                continue
            paddock_counts = counts_by_paddock.setdefault(paddock_id, {})
            paddock_counts[target_id] = paddock_counts.get(target_id, 0) + addition

        existing_target_count = int(old_context.get(target_id, {}).get("head_count", 0))
        final_context = cls.balance_context(
            mob,
            overrides={
                source_id: 0,
                target_id: existing_target_count + int(target_head_count or 0),
            },
        )
        cls.rewrite_session_assignments(session, counts_by_paddock, context=final_context)

    @classmethod
    def _session_split_weights(
        cls,
        session: GrazingSession,
        counts_by_paddock: dict[str, dict[str, int]],
        context: dict[str, dict],
    ) -> list[tuple[str, Decimal]]:
        assigned_lsu_by_paddock: dict[str, Decimal] = {}
        for paddock_id, group_counts in counts_by_paddock.items():
            assigned_lsu = Decimal("0")
            for group_id, head_count in group_counts.items():
                meta = context.get(str(group_id))
                if meta:
                    assigned_lsu += Decimal(int(head_count or 0)) * Decimal(str(meta["lsu_per_head"]))
            if assigned_lsu > 0:
                assigned_lsu_by_paddock[paddock_id] = assigned_lsu
        if assigned_lsu_by_paddock:
            return sorted(assigned_lsu_by_paddock.items())
        return [
            (str(allocation.paddock_id), Decimal(str(allocation.allocation_fraction or 0)))
            for allocation in cls._sorted_allocations(session)
        ]
