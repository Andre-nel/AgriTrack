from datetime import datetime, timezone
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db
from app.models import Farm, Mob, MobEvent, MovementEvent, MovementEventMob, Paddock
from app.models.animal_group import AnimalGroupBalance
from app.models.movement import MobLineage, MovementEventKind, MovementRole
from app.models.stock_ledger import StockEventType
from app.services.grazing_history_service import GrazingHistoryService
from app.services.grazing_service import GrazingService
from app.services.mob_event_service import MobEventService
from app.services.mob_service import MobService
from app.services.reporting_service import ReportingService
from app.services.stock_service import StockService
from app.services.validation_service import ValidationService


class MovementService:
    FRACTION_QUANT = Decimal("0.0001")
    GROUP_FRACTION_QUANT = Decimal("0.000001")

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

        source_balances = {
            str(balance.animal_group_type_id): int(balance.head_count)
            for balance in source_mob.balances
            if int(balance.head_count) > 0
        }
        if not source_balances:
            raise ValueError("Source mob has no stock to split")

        by_mob: dict[str, dict[str, int]] = defaultdict(dict)
        allocated_totals = {group_id: 0 for group_id in source_balances}

        for split in splits:
            name = (split.get("name") or "").strip()
            if not name:
                raise ValueError("Each split requires a mob name")
            if name.lower() == source_mob.name.lower():
                raise ValueError("Split mob name cannot match the source mob name")

            groups = split.get("groups", [])
            if not groups:
                raise ValueError(f"Split mob '{name}' must include at least one group allocation")

            mob_group_totals = by_mob[name]
            for group in groups:
                group_id = str(group.get("animal_group_type_id") or "").strip()
                if group_id not in source_balances:
                    raise ValueError("Split contains an invalid animal group for this source mob")

                try:
                    qty = int(group.get("quantity", 0))
                except (TypeError, ValueError):
                    raise ValueError("Split quantities must be whole numbers")
                if qty <= 0:
                    raise ValueError("Split quantities must be greater than 0")

                mob_group_totals[group_id] = mob_group_totals.get(group_id, 0) + qty
                allocated_totals[group_id] += qty

        normalized = []
        for name, groups in by_mob.items():
            normalized_groups = [
                {"animal_group_type_id": group_id, "quantity": qty}
                for group_id, qty in groups.items()
            ]
            normalized.append({"name": name, "groups": normalized_groups})

        if len(normalized) < 1:
            raise ValueError("Split must create at least one mob")

        has_allocated_stock = False
        for group_id, source_qty in source_balances.items():
            allocated_qty = allocated_totals.get(group_id, 0)
            if allocated_qty > source_qty:
                raise ValueError(
                    "Split allocations cannot exceed source stock "
                    f"(group {group_id}: allocated {allocated_qty}, available {source_qty})"
                )
            if allocated_qty > 0:
                has_allocated_stock = True

        if not has_allocated_stock:
            raise ValueError("Split must allocate stock to at least one new mob")

        return normalized

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
            context[str(balance.animal_group_type_id)] = {
                "head_count": head_count,
                "lsu_per_head": lsu_per_head,
                "label": f"{group.species} {group.breed} {group.sex} {group.age_class}",
            }
        return context

    @staticmethod
    def _parse_count_quantity(value) -> int:
        if value is None or value == "":
            return 0
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            raise ValueError("Count allocations must be whole numbers")
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
                    raise ValueError("Each count allocation requires an animal group")
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
        ValidationService.validate_allocations(allocations)
        return allocations

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

        source_balance_rows = {
            str(balance.animal_group_type_id): balance
            for balance in source_mob.balances
            if int(balance.head_count) > 0
        }
        source_balances = {
            group_id: int(balance.head_count) for group_id, balance in source_balance_rows.items()
        }
        group_labels = {
            group_id: MovementService._animal_group_log_label(balance.animal_group_type)
            for group_id, balance in source_balance_rows.items()
        }
        if not source_balances:
            raise ValueError("Source mob has no stock to transfer")

        normalized: dict[str, int] = {}
        for item in transfers:
            group_id = str(item.get("animal_group_type_id") or "").strip()
            if not group_id:
                raise ValueError("Each transfer row requires a group")
            if group_id not in source_balances:
                raise ValueError("Transfer contains an invalid animal group for this source mob")
            try:
                quantity = int(item.get("quantity", 0))
            except (TypeError, ValueError):
                raise ValueError("Transfer quantities must be whole numbers")
            if quantity <= 0:
                raise ValueError("Transfer quantities must be greater than 0")
            normalized[group_id] = normalized.get(group_id, 0) + quantity

        for group_id, quantity in normalized.items():
            available = source_balances[group_id]
            if quantity > available:
                raise ValueError(
                    "Transfer quantities cannot exceed source stock "
                    f"(group {group_id}: transfer {quantity}, available {available})"
                )

        note_text = " ".join((note or "").strip().split())
        source_note = note or f"transfer to {destination_mob.name}"
        destination_note = note or f"transfer from {source_mob.name}"
        for group_id, quantity in normalized.items():
            StockService.adjust_stock(
                mob_id=source_mob.id,
                farm_id=source_mob.farm_id,
                animal_group_type_id=group_id,
                event_type=StockEventType.transfer_out,
                quantity=quantity,
                note=source_note,
                event_time=when,
                sync_grazing_history=False,
            )
            StockService.adjust_stock(
                mob_id=destination_mob.id,
                farm_id=destination_mob.farm_id,
                animal_group_type_id=group_id,
                event_type=StockEventType.transfer_in,
                quantity=quantity,
                note=destination_note,
                event_time=when,
                sync_grazing_history=False,
            )

        transfer_summary = MovementService._transfer_summary(group_labels, normalized)
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
                qty = int(group["quantity"])
                group_id = group["animal_group_type_id"]
                StockService.adjust_stock(
                    mob_id=source_mob.id,
                    farm_id=source_mob.farm_id,
                    animal_group_type_id=group_id,
                    event_type=StockEventType.transfer_out,
                    quantity=qty,
                    note="split out",
                    event_time=when,
                    sync_grazing_history=False,
                )
                StockService.adjust_stock(
                    mob_id=new_mob.id,
                    farm_id=source_mob.farm_id,
                    animal_group_type_id=group_id,
                    event_type=StockEventType.transfer_in,
                    quantity=qty,
                    note="split in",
                    event_time=when,
                    sync_grazing_history=False,
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
            balances = AnimalGroupBalance.query.filter_by(mob_id=source.id).all()
            for balance in balances:
                if balance.head_count == 0:
                    continue
                StockService.adjust_stock(
                    mob_id=source.id,
                    farm_id=farm_id,
                    animal_group_type_id=balance.animal_group_type_id,
                    event_type=StockEventType.transfer_out,
                    quantity=balance.head_count,
                    note="merge out",
                    event_time=when,
                    sync_grazing_history=False,
                )
                StockService.adjust_stock(
                    mob_id=result_mob.id,
                    farm_id=farm_id,
                    animal_group_type_id=balance.animal_group_type_id,
                    event_type=StockEventType.transfer_in,
                    quantity=balance.head_count,
                    note="merge in",
                    event_time=when,
                    sync_grazing_history=False,
                )
            MobService.archive_mob(source, when=when)

        for source in source_mobs:
            GrazingHistoryService.sync_live_history_for_mob(source, effective_at=when)
        GrazingHistoryService.sync_live_history_for_mob(result_mob, effective_at=when)
        return result_mob
