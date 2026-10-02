from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db
from app.models import (
    Farm,
    GrazingAllocation,
    GrazingSession,
    Mob,
    MobEvent,
    MovementEvent,
    MovementEventMob,
    Paddock,
)
from app.models.animal_group import AnimalGroupBalance
from app.models.movement import MobLineage, MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType
from app.services.allocation_distribution_service import AllocationDistributionService
from app.services.cohort_service import CohortService
from app.services.grazing_history_service import GrazingHistoryService
from app.services.grazing_service import GrazingService
from app.services.mob_event_service import MobEventService
from app.services.mob_service import MobService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService
from app.services.stock_groups import apportion_counts
from app.services.validation_service import ValidationService


class MovementService:
    FRACTION_QUANT = Decimal("0.0001")
    GROUP_FRACTION_QUANT = Decimal("0.000001")

    @staticmethod
    def _positive_balance_rows(mob: Mob) -> list[AnimalGroupBalance]:
        return [row for row in mob.balances if int(row.head_count or 0) > 0]

    @classmethod
    def _resolve_balance_rows(
        cls,
        mob: Mob,
        *,
        selector: str | None = None,
        cohort_id: str | None = None,
        animal_group_type_id: str | None = None,
        animal_group_type: dict | None = None,
    ) -> list[AnimalGroupBalance]:
        rows = cls._positive_balance_rows(mob)
        cohort_key = str(cohort_id or "").strip()
        selector_key = str(selector or "").strip()
        group_key = str(animal_group_type_id or "").strip()
        if group_key.startswith("cohort:"):
            # Older web forms stored a cohort selector in the group-id field.
            group_key = ""
        if selector_key.startswith("cohort:"):
            selected_cohort_key = selector_key.split(":", 1)[1].strip()
            if cohort_key and cohort_key != selected_cohort_key:
                raise ValueError("Conflicting animal cohort selectors")
            cohort_key = selected_cohort_key
        elif selector_key:
            if group_key and group_key != selector_key:
                raise ValueError("Conflicting animal group selectors")
            group_key = selector_key

        if cohort_key:
            matches = [row for row in rows if str(row.cohort_id or "") == cohort_key]
            if matches and group_key and str(matches[0].animal_group_type_id) != group_key:
                raise ValueError("Selected cohort does not match the animal group type")
        elif group_key:
            matches = [row for row in rows if str(row.animal_group_type_id) == group_key]
        elif isinstance(animal_group_type, dict):
            species = StockService.normalize_species(str(animal_group_type.get("species") or ""))
            breed = " ".join(str(animal_group_type.get("breed") or "").strip().split())
            sex = StockService.normalize_sex(species, str(animal_group_type.get("sex") or ""))
            age_class = StockService.normalize_age_class(species, str(animal_group_type.get("age_class") or ""))
            matches = [
                row for row in rows
                if (row.animal_group_type.species, row.animal_group_type.breed,
                    row.animal_group_type.sex, row.animal_group_type.age_class)
                == (species, breed, sex, age_class)
            ]
        else:
            matches = []
        if not matches:
            raise ValueError("Transfer contains an invalid animal cohort for this source mob")
        return sorted(matches, key=lambda row: str(row.id))

    @classmethod
    def _normalize_stock_requests(cls, source_mob: Mob, requests: list[tuple[str, dict]]) -> dict:
        """Reserve explicit cohorts, then apportion aggregate requests without overdraw."""
        source_rows = cls._positive_balance_rows(source_mob)
        by_id = {str(row.id): row for row in source_rows}
        remaining = {key: int(row.head_count) for key, row in by_id.items()}
        result = {}
        proportional = set()
        aggregate = {}
        for destination, item in requests:
            if not isinstance(item, dict):
                raise ValueError("Each transfer row must identify an animal type and quantity")
            result.setdefault(destination, {})
            rows = cls._resolve_balance_rows(
                source_mob, selector=item.get("stock_selector") or item.get("animal_group_type_id"),
                cohort_id=item.get("cohort_id"), animal_group_type_id=item.get("animal_group_type_id"),
                animal_group_type=item.get("animal_group_type"),
            )
            quantity = ValidationService.parse_whole_number(item.get("quantity", 0), "Transfer quantities")
            if quantity <= 0:
                raise ValueError("Transfer quantities must be greater than 0")
            selector = str(item.get("stock_selector") or item.get("animal_group_type_id") or "").strip()
            explicit = bool(str(item.get("cohort_id") or "").strip()) or selector.startswith("cohort:")
            if explicit:
                key = str(rows[0].id)
                remaining[key] -= quantity
                if remaining[key] < 0:
                    raise ValueError("Transfer quantities cannot exceed source stock")
                result[destination][key] = result[destination].get(key, 0) + quantity
            else:
                group_id = str(rows[0].animal_group_type_id)
                destinations = aggregate.setdefault(group_id, {})
                destinations[destination] = destinations.get(destination, 0) + quantity

        for group_id, destinations in aggregate.items():
            candidates = {
                key: remaining[key] for key, row in by_id.items()
                if str(row.animal_group_type_id) == group_id and remaining[key] > 0
            }
            requested = sum(destinations.values())
            if requested > sum(candidates.values()):
                raise ValueError("Transfer quantities cannot exceed source stock")
            # Fix the source remainder before distributing rounded portions to destinations.
            pool = apportion_counts(requested, list(candidates.items()))
            for destination, quantity in destinations.items():
                shares = apportion_counts(quantity, [(key, count) for key, count in pool.items() if count > 0])
                for key, count in shares.items():
                    if count:
                        result[destination][key] = result[destination].get(key, 0) + count
                        pool[key] -= count
                        if len(candidates) > 1:
                            proportional.add((destination, key))

        return {
            destination: [
                {"balance_id": key, "animal_group_type_id": str(by_id[key].animal_group_type_id),
                 "cohort_id": str(by_id[key].cohort_id) if by_id[key].cohort_id else None,
                 "quantity": count, "proportional": (destination, key) in proportional}
                for key, count in sorted(portions.items()) if count > 0
            ]
            for destination, portions in result.items()
        }

    @staticmethod
    def _normalize_species_key(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

    @staticmethod
    def _whole_head_count(value) -> int:
        numeric = float(value or 0)
        rounded = int(round(numeric))
        if abs(numeric - rounded) > 0.0001:
            raise ValueError(
                "This species cannot be dragged because its current allocation "
                "contains fractional head counts. Move the mob from its detail page instead."
            )
        return rounded

    @classmethod
    def _active_session_count_map(cls, session: GrazingSession) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
        context = AllocationDistributionService.balance_context(session.mob)
        counts = AllocationDistributionService.snapshot_session_counts(session, context=context)
        species_by_group_id = {
            str(group_id): meta["group"].species
            for group_id, meta in context.items()
        }
        return defaultdict(dict, {paddock_id: dict(group_counts) for paddock_id, group_counts in counts.items()}), species_by_group_id

    @staticmethod
    def _count_allocations_from_map(counts_by_paddock: dict[str, dict[str, int]]) -> list[dict]:
        allocations = []
        for paddock_id in sorted(counts_by_paddock):
            group_counts = [
                {"animal_group_type_id": group_id, "head_count": head_count}
                for group_id, head_count in sorted(counts_by_paddock[paddock_id].items())
                if head_count > 0
            ]
            if group_counts:
                allocations.append({"paddock_id": paddock_id, "group_counts": group_counts})
        return allocations

    @staticmethod
    def _destination_farm_for_count_map(
        mob: Mob,
        target_paddock: Paddock,
        counts_by_paddock: dict[str, dict[str, int]],
    ) -> str:
        paddock_ids = [
            paddock_id
            for paddock_id, group_counts in counts_by_paddock.items()
            if any(head_count > 0 for head_count in group_counts.values())
        ]
        if not paddock_ids:
            return str(target_paddock.farm_id)

        paddocks = Paddock.query.filter(Paddock.id.in_(paddock_ids)).all()
        farm_ids = {str(paddock.farm_id) for paddock in paddocks}
        if len(farm_ids) == 1:
            return next(iter(farm_ids))
        if str(mob.farm_id) in farm_ids:
            return str(mob.farm_id)
        if str(target_paddock.farm_id) in farm_ids:
            return str(target_paddock.farm_id)
        return sorted(farm_ids)[0]

    @staticmethod
    def _resolve_move_destination_farm_id(
        mob: Mob,
        allocations: list[dict],
        destination_farm_id: str | None = None,
        allow_cross_farm_allocations: bool = False,
    ) -> str:
        farm_id = str(destination_farm_id or mob.farm_id).strip()
        if not farm_id:
            raise ValueError("Destination farm is required")

        if not Farm.query.filter_by(id=farm_id).first():
            raise ValueError("Destination farm is invalid")

        paddock_ids = [str(item.get("paddock_id") or "").strip() for item in allocations]
        if not all(paddock_ids):
            raise ValueError("Each allocation row requires a paddock")

        unique_paddock_ids = set(paddock_ids)
        if len(unique_paddock_ids) != len(paddock_ids):
            raise ValueError("Duplicate paddock rows are not allowed")

        paddocks = Paddock.query.filter(Paddock.id.in_(unique_paddock_ids)).all()
        if len(paddocks) != len(unique_paddock_ids):
            raise ValueError("One or more selected paddocks are invalid")

        if not allow_cross_farm_allocations and any(str(paddock.farm_id) != farm_id for paddock in paddocks):
            raise ValueError("Selected paddocks do not belong to the chosen destination farm")

        if str(mob.farm_id) != farm_id:
            conflict = Mob.query.filter(
                Mob.farm_id == farm_id,
                Mob.status == "active",
                Mob.name == mob.name,
                Mob.id != mob.id,
            ).first()
            if conflict:
                raise ValueError("Active mob name already exists on the destination farm")

        return farm_id

    @staticmethod
    def _normalize_splits(source_mob: Mob, splits: list[dict]) -> list[dict]:
        if not splits:
            raise ValueError("At least one split mob is required")
        if not MovementService._positive_balance_rows(source_mob):
            raise ValueError("Source mob has no stock to split")
        requests = []
        for split in splits:
            if not isinstance(split, dict):
                raise ValueError("Each split requires a mob name and stock allocations")
            name = (split.get("name") or "").strip()
            if not name:
                raise ValueError("Each split requires a mob name")
            if name.lower() == source_mob.name.lower():
                raise ValueError("Split mob name cannot match the source mob name")

            groups = split.get("groups", [])
            if not groups:
                raise ValueError(f"Split mob '{name}' must include at least one group allocation")

            for group in groups:
                requests.append((name, group))
        allocations = MovementService._normalize_stock_requests(source_mob, requests)
        return [{"name": name, "groups": groups} for name, groups in allocations.items()]

    @staticmethod
    def _normalize_allocation_mode(allocation_mode: str | None, allocations: list[dict]) -> str:
        mode = (allocation_mode or "").strip().lower()
        if not mode:
            mode = "counts" if any(item.get("group_counts") for item in allocations or []) else "percentage"
        if mode not in {"percentage", "counts"}:
            raise ValueError("allocation_mode must be either 'percentage' or 'counts'")
        return mode

    @staticmethod
    def _source_balance_context(mob: Mob) -> dict[str, dict]:
        context = {}
        for balance in mob.balances:
            head_count = int(balance.head_count or 0)
            if head_count <= 0:
                continue
            group = balance.animal_group_type
            lsu_per_head = Decimal(
                str(ReportingService.group_lsu_per_head(group.species, group.sex, group.age_class))
            )
            group_id = str(balance.animal_group_type_id)
            if group_id in context:
                context[group_id]["head_count"] += head_count
            else:
                context[group_id] = {
                    "head_count": head_count,
                    "lsu_per_head": lsu_per_head,
                    "label": f"{group.species} {group.breed} {group.sex} {group.age_class}",
                }
        return context

    @staticmethod
    def _parse_count_quantity(value) -> int:
        if value is None or value == "":
            return 0
        quantity = ValidationService.parse_whole_number(value, "Count allocations")
        if quantity < 0:
            raise ValueError("Count allocations cannot be negative")
        return quantity

    @staticmethod
    def _animal_group_log_label(group) -> str:
        return f"{group.species} | {group.breed} | {group.sex} | {group.age_class}"

    @staticmethod
    def _transfer_summary(group_labels: dict[str, str], transfers: dict[str, int]) -> str:
        parts = [
            f"{quantity} head {group_labels.get(group_id, group_id)}"
            for group_id, quantity in transfers.items()
        ]
        if len(parts) <= 1:
            return parts[0] if parts else "stock"
        return f"{', '.join(parts[:-1])} and {parts[-1]}"

    @staticmethod
    def _normalize_count_allocations(mob: Mob, allocations: list[dict]) -> list[dict]:
        if not allocations:
            raise ValueError("At least one paddock allocation is required")

        source_balances = MovementService._source_balance_context(mob)
        if not source_balances:
            raise ValueError("Source mob has no stock to allocate by count")

        mob_total_lsu = sum(
            row["lsu_per_head"] * Decimal(row["head_count"]) for row in source_balances.values()
        )
        if mob_total_lsu <= 0:
            raise ValueError("Count-based allocation requires the mob to have positive LSU")

        group_totals = {group_id: 0 for group_id in source_balances}
        paddock_order: list[str] = []
        row_totals_by_paddock: dict[str, dict[str, int]] = {}

        for item in allocations:
            paddock_id = str(item.get("paddock_id") or "").strip()
            if not paddock_id:
                raise ValueError("Each count allocation row requires a paddock")
            if paddock_id not in row_totals_by_paddock:
                row_totals_by_paddock[paddock_id] = {}
                paddock_order.append(paddock_id)

            seen_groups = set()
            row_has_positive_count = False
            for group_count in item.get("group_counts") or []:
                group_id = str(group_count.get("animal_group_type_id") or "").strip()
                if not group_id:
                    matches = MovementService._resolve_balance_rows(
                        mob, animal_group_type=group_count.get("animal_group_type")
                    )
                    group_id = str(matches[0].animal_group_type_id)
                if group_id in seen_groups:
                    raise ValueError("Duplicate animal groups are not allowed within a count row")
                seen_groups.add(group_id)
                if group_id not in source_balances:
                    raise ValueError("Count allocation contains an invalid animal group for this mob")

                quantity = MovementService._parse_count_quantity(group_count.get("head_count"))
                if quantity == 0:
                    continue

                row_has_positive_count = True
                group_totals[group_id] += quantity
                row_totals = row_totals_by_paddock[paddock_id]
                row_totals[group_id] = row_totals.get(group_id, 0) + quantity

            if not row_has_positive_count:
                raise ValueError("Each count allocation row must assign at least one animal")

        for group_id, source in source_balances.items():
            allocated = group_totals[group_id]
            available = source["head_count"]
            if allocated != available:
                raise ValueError(
                    "Count allocations must cover the whole mob "
                    f"({source['label']}: allocated {allocated}, available {available})"
                )

        normalized_rows = []
        for paddock_id in paddock_order:
            row_group_counts = []
            assigned_lsu = Decimal("0")
            for group_id, quantity in row_totals_by_paddock[paddock_id].items():
                source = source_balances[group_id]
                group_fraction = (
                    Decimal(quantity) / Decimal(source["head_count"])
                ).quantize(MovementService.GROUP_FRACTION_QUANT, rounding=ROUND_HALF_UP)
                group_lsu = Decimal(quantity) * source["lsu_per_head"]
                assigned_lsu += group_lsu
                row_group_counts.append(
                    {
                        "animal_group_type_id": group_id,
                        "head_count": quantity,
                        "group_fraction": str(group_fraction),
                        "assigned_lsu": str(group_lsu),
                    }
                )

            normalized_rows.append(
                {
                    "paddock_id": paddock_id,
                    "assigned_lsu": assigned_lsu,
                    "group_counts": row_group_counts,
                }
            )

        for row in normalized_rows:
            row["allocation_fraction"] = (
                row["assigned_lsu"] / mob_total_lsu
            ).quantize(MovementService.FRACTION_QUANT, rounding=ROUND_HALF_UP)

        total_fraction = sum(row["allocation_fraction"] for row in normalized_rows)
        delta = Decimal("1") - total_fraction
        if delta:
            largest = max(normalized_rows, key=lambda row: row["assigned_lsu"])
            largest["allocation_fraction"] += delta
            if largest["allocation_fraction"] <= 0:
                raise ValueError("Count allocations cannot be converted to valid percentages")

        return [
            {
                "paddock_id": row["paddock_id"],
                "allocation_fraction": str(row["allocation_fraction"]),
                "group_counts": row["group_counts"],
            }
            for row in normalized_rows
        ]

    @staticmethod
    def _normalize_move_allocations(
        mob: Mob,
        allocations: list[dict],
        allocation_mode: str | None = None,
    ) -> list[dict]:
        mode = MovementService._normalize_allocation_mode(allocation_mode, allocations)
        if mode == "counts":
            return MovementService._normalize_count_allocations(mob, allocations)
        return MovementService._normalize_percentage_allocations(mob, allocations)

    @staticmethod
    def _normalize_percentage_allocations(mob: Mob, allocations: list[dict]) -> list[dict]:
        ValidationService.validate_allocations(allocations)
        context = AllocationDistributionService.balance_context(mob)
        if not context:
            return allocations
        counts_by_paddock = AllocationDistributionService.count_map_for_fraction_allocations(
            mob,
            allocations,
            context=context,
        )
        exact_allocations = AllocationDistributionService.allocations_from_count_map(
            mob,
            counts_by_paddock,
            context=context,
        )
        if not exact_allocations:
            return allocations
        return exact_allocations

    @staticmethod
    def move_mob(
        mob: Mob,
        allocations: list[dict],
        destination_farm_id: str | None = None,
        when=None,
        allocation_mode: str | None = None,
        apply_open_gate_network: bool = True,
        allow_cross_farm_allocations: bool = False,
    ):
        when = when or datetime.now(timezone.utc)
        allocation_mode = MovementService._normalize_allocation_mode(allocation_mode, allocations)
        allocations = MovementService._normalize_move_allocations(
            mob=mob,
            allocations=allocations,
            allocation_mode=allocation_mode,
        )
        resolved_destination_farm_id = MovementService._resolve_move_destination_farm_id(
            mob=mob,
            allocations=allocations,
            destination_farm_id=destination_farm_id,
            allow_cross_farm_allocations=allow_cross_farm_allocations,
        )
        if apply_open_gate_network:
            from app.services.gate_service import GateService

            allocations = GateService.allocations_for_open_gate_network(
                resolved_destination_farm_id,
                allocations,
            )
            if any(item.get("group_counts") for item in allocations or []):
                allocations = MovementService._normalize_count_allocations(mob, allocations)
            elif allocation_mode == "percentage":
                allocations = MovementService._normalize_percentage_allocations(mob, allocations)

        GrazingService.close_open_session(mob_id=mob.id, end_at=when)
        mob.farm_id = resolved_destination_farm_id
        session = GrazingService.open_session(
            farm_id=resolved_destination_farm_id,
            mob_id=mob.id,
            start_at=when,
            allocations=allocations,
        )

        event = MovementEvent(
            farm_id=resolved_destination_farm_id,
            event_time=when,
            event_kind=MovementEventKind.move,
        )
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(movement_event_id=event.id, mob_id=mob.id, role=MovementRole.source)
        )

        return session

    @classmethod
    def move_species_between_paddocks(
        cls,
        *,
        source_paddock_id: str,
        target_paddock_id: str,
        species: str,
        when=None,
    ) -> dict:
        source_id = str(source_paddock_id or "").strip()
        target_id = str(target_paddock_id or "").strip()
        species_key = cls._normalize_species_key(species)
        if not source_id or not target_id:
            raise ValueError("Source and target paddocks are required")
        if source_id == target_id:
            raise ValueError("Drop the species on a different paddock")
        if not species_key:
            raise ValueError("Species is required")

        source_paddock = Paddock.query.filter_by(id=source_id, status="active").first()
        if source_paddock is None:
            raise ValueError("Source paddock is invalid")
        target_paddock = Paddock.query.filter_by(id=target_id, status="active").first()
        if target_paddock is None:
            raise ValueError("Target paddock is invalid")

        sessions = (
            GrazingSession.query.join(Mob)
            .join(GrazingAllocation)
            .filter(
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
                GrazingAllocation.paddock_id == source_id,
            )
            .all()
        )

        moved_mob_count = 0
        moved_head_count = 0
        event_time = when or datetime.now(timezone.utc)
        for session in sessions:
            counts_by_paddock, species_by_group_id = cls._active_session_count_map(session)
            source_counts = counts_by_paddock.get(source_id, {})
            move_counts = {
                group_id: head_count
                for group_id, head_count in source_counts.items()
                if head_count > 0
                and cls._normalize_species_key(species_by_group_id.get(group_id)) == species_key
            }
            if not move_counts:
                continue

            target_counts = counts_by_paddock.setdefault(target_id, {})
            for group_id, head_count in move_counts.items():
                next_source_count = source_counts.get(group_id, 0) - head_count
                if next_source_count > 0:
                    source_counts[group_id] = next_source_count
                else:
                    source_counts.pop(group_id, None)
                target_counts[group_id] = target_counts.get(group_id, 0) + head_count
                moved_head_count += head_count

            if not source_counts:
                counts_by_paddock.pop(source_id, None)

            allocations = cls._count_allocations_from_map(counts_by_paddock)
            destination_farm_id = cls._destination_farm_for_count_map(
                session.mob,
                target_paddock,
                counts_by_paddock,
            )
            cls.move_mob(
                mob=session.mob,
                allocations=allocations,
                destination_farm_id=destination_farm_id,
                when=event_time,
                allocation_mode="counts",
                allow_cross_farm_allocations=True,
            )
            moved_mob_count += 1

        if moved_mob_count == 0:
            raise ValueError("No active stock for that species was found in the source paddock")

        return {
            "moved_mob_count": moved_mob_count,
            "moved_head_count": moved_head_count,
            "species": species,
            "source_paddock_id": source_id,
            "target_paddock_id": target_id,
        }

    @staticmethod
    def _post_stock_transfer_group(
        source_mob: Mob, destination_mob: Mob, portions: list[dict], *, when,
        source_note: str, destination_note: str, movement_event_id: str | None = None,
        sync_source_allocation: bool = True,
    ) -> None:
        """Post one type as a batch so cohort rounding never changes paddock rounding."""
        group_id = portions[0]["animal_group_type_id"]
        total = sum(row["quantity"] for row in portions)
        if sync_source_allocation:
            AllocationDistributionService.apply_stock_delta(
                source_mob, animal_group_type_id=group_id, delta=-total,
                final_head_count=StockService.group_head_count(source_mob.id, group_id) - total,
                effective_at=when,
            )
        AllocationDistributionService.apply_stock_delta(
            destination_mob, animal_group_type_id=group_id, delta=total,
            final_head_count=StockService.group_head_count(destination_mob.id, group_id) + total,
            effective_at=when,
        )
        for row in portions:
            balance = db.session.get(AnimalGroupBalance, row["balance_id"])
            if balance is None:
                raise ValueError("Transfer source balance is no longer available")
            transfer_cohort = CohortService.cohort_for_partial_transfer(
                balance, quantity=row["quantity"], movement_event_id=movement_event_id,
                destination_farm_id=str(destination_mob.farm_id),
            )
            StockService.adjust_stock(
                mob_id=source_mob.id, farm_id=source_mob.farm_id,
                animal_group_type_id=group_id, cohort_id=balance.cohort_id,
                event_type=StockEventType.transfer_out, quantity=row["quantity"],
                note=StockService.proportional_note(source_note) if row.get("proportional") else source_note,
                event_time=when, sync_grazing_history=False,
                sync_allocation_distribution=False,
            )
            StockService.adjust_stock(
                mob_id=destination_mob.id, farm_id=destination_mob.farm_id,
                animal_group_type_id=group_id, cohort_id=transfer_cohort.id,
                event_type=StockEventType.transfer_in, quantity=row["quantity"],
                note=StockService.proportional_note(destination_note) if row.get("proportional") else destination_note,
                event_time=when, sync_grazing_history=False,
                sync_allocation_distribution=False,
            )

    @staticmethod
    def transfer_stock_between_mobs(
        source_mob: Mob,
        destination_mob: Mob,
        transfers: list[dict],
        destination_farm_id: str | None = None,
        note: str | None = None,
        when=None,
    ) -> None:
        when = when or datetime.now(timezone.utc)
        if not transfers:
            raise ValueError("At least one transfer row is required")
        if str(source_mob.id) == str(destination_mob.id):
            raise ValueError("Destination mob must be different from source mob")
        if source_mob.status != "active":
            raise ValueError("Source mob must be active")
        if destination_mob.status != "active":
            raise ValueError("Destination mob must be active")

        selected_destination_farm_id = str(destination_farm_id or destination_mob.farm_id).strip()
        if str(destination_mob.farm_id) != selected_destination_farm_id:
            raise ValueError("Destination mob is invalid for the selected destination farm")

        source_balance_rows = MovementService._positive_balance_rows(source_mob)
        if not source_balance_rows:
            raise ValueError("Source mob has no stock to transfer")

        normalized = MovementService._normalize_stock_requests(
            source_mob, [("destination", item) for item in transfers]
        )["destination"]
        by_group = defaultdict(list)
        for item in normalized:
            by_group[item["animal_group_type_id"]].append(item)

        note_text = " ".join((note or "").strip().split())
        source_note = note or f"transfer to {destination_mob.name}"
        destination_note = note or f"transfer from {source_mob.name}"
        summary_labels = {}
        summary_quantities = {}
        for group_id, portions in by_group.items():
            balance = db.session.get(AnimalGroupBalance, portions[0]["balance_id"])
            summary_labels[group_id] = MovementService._animal_group_log_label(balance.animal_group_type)
            summary_quantities[group_id] = sum(row["quantity"] for row in portions)
            MovementService._post_stock_transfer_group(
                source_mob, destination_mob, portions, when=when,
                source_note=source_note, destination_note=destination_note,
            )

        transfer_summary = MovementService._transfer_summary(summary_labels, summary_quantities)
        source_description = f"Stock transfer out to {destination_mob.name}: {transfer_summary}."
        destination_description = f"Stock transfer in from {source_mob.name}: {transfer_summary}."
        if note_text:
            source_description = f"{source_description} Note: {note_text}"
            destination_description = f"{destination_description} Note: {note_text}"

        MobEventService.create_event(
            mob_id=source_mob.id,
            farm_id=source_mob.farm_id,
            description=source_description,
            raw_tags="stock,transfer out,stock adjustment",
            event_at=when,
        )
        MobEventService.create_event(
            mob_id=destination_mob.id,
            farm_id=destination_mob.farm_id,
            description=destination_description,
            raw_tags="stock,transfer in,stock adjustment",
            event_at=when,
        )

        GrazingHistoryService.sync_live_history_for_mob(source_mob, effective_at=when)
        GrazingHistoryService.sync_live_history_for_mob(destination_mob, effective_at=when)

    @staticmethod
    def split_mob(source_mob: Mob, splits: list[dict], when=None):
        if source_mob.status != "active":
            raise ValueError("Source mob must be active")
        splits = MovementService._normalize_splits(source_mob=source_mob, splits=splits)
        when = when or datetime.now(timezone.utc)
        event = MovementEvent(farm_id=source_mob.farm_id, event_time=when, event_kind=MovementEventKind.split)
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(
                movement_event_id=event.id, mob_id=source_mob.id, role=MovementRole.source
            )
        )

        created = []
        transfer_batches = defaultdict(list)
        parent_events = list(source_mob.events)
        for split in splits:
            new_mob = Mob(farm_id=source_mob.farm_id, name=split["name"], status="active")
            db.session.add(new_mob)
            db.session.flush()
            created.append(new_mob)
            db.session.add(
                MovementEventMob(
                    movement_event_id=event.id,
                    mob_id=new_mob.id,
                    role=MovementRole.result,
                )
            )
            db.session.add(
                MobLineage(
                    parent_mob_id=source_mob.id,
                    child_mob_id=new_mob.id,
                    movement_event_id=event.id,
                )
            )
            for parent_event in parent_events:
                db.session.add(
                    MobEvent(
                        mob_id=new_mob.id,
                        farm_id=new_mob.farm_id,
                        event_at=parent_event.event_at,
                        tags_csv=parent_event.tags_csv,
                        description=parent_event.description,
                    )
                )

            for group in split.get("groups", []):
                transfer_batches[group["animal_group_type_id"]].append((new_mob, group))

        for group_id, batches in transfer_batches.items():
            total = sum(portion["quantity"] for _destination, portion in batches)
            AllocationDistributionService.apply_stock_delta(
                source_mob, animal_group_type_id=group_id, delta=-total,
                final_head_count=StockService.group_head_count(source_mob.id, group_id) - total,
                effective_at=when,
            )
            by_destination = defaultdict(list)
            for destination, portion in batches:
                by_destination[destination].append(portion)
            for destination, portions in by_destination.items():
                MovementService._post_stock_transfer_group(
                    source_mob, destination, portions, when=when,
                    source_note="split out", destination_note="split in",
                    movement_event_id=str(event.id), sync_source_allocation=False,
                )

        GrazingHistoryService.sync_live_history_for_mob(source_mob, effective_at=when)
        for mob in created:
            GrazingHistoryService.sync_live_history_for_mob(mob, effective_at=when)
        return created

    @staticmethod
    def merge_mobs(source_mobs: list[Mob], result_name: str, when=None):
        when = when or datetime.now(timezone.utc)
        if not source_mobs:
            raise ValueError("At least one source mob is required")

        farm_id = source_mobs[0].farm_id
        result_mob = Mob(farm_id=farm_id, name=result_name, status="active")
        db.session.add(result_mob)
        db.session.flush()

        event = MovementEvent(farm_id=farm_id, event_time=when, event_kind=MovementEventKind.merge)
        db.session.add(event)
        db.session.flush()
        db.session.add(
            MovementEventMob(
                movement_event_id=event.id, mob_id=result_mob.id, role=MovementRole.result
            )
        )

        for source in source_mobs:
            db.session.add(
                MovementEventMob(
                    movement_event_id=event.id, mob_id=source.id, role=MovementRole.source
                )
            )
            db.session.add(
                MobLineage(
                    parent_mob_id=source.id,
                    child_mob_id=result_mob.id,
                    movement_event_id=event.id,
                )
            )
            by_group = defaultdict(list)
            for balance in MovementService._positive_balance_rows(source):
                by_group[str(balance.animal_group_type_id)].append({
                    "balance_id": str(balance.id),
                    "animal_group_type_id": str(balance.animal_group_type_id),
                    "quantity": int(balance.head_count),
                })
            for portions in by_group.values():
                MovementService._post_stock_transfer_group(
                    source, result_mob, portions, when=when,
                    source_note="merge out", destination_note="merge in",
                    movement_event_id=str(event.id),
                )
            MobService.archive_mob(source, when=when)

        for source in source_mobs:
            GrazingHistoryService.sync_live_history_for_mob(source, effective_at=when)
        GrazingHistoryService.sync_live_history_for_mob(result_mob, effective_at=when)
        return result_mob
