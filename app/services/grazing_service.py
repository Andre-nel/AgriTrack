from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models import GrazingAllocation, GrazingAllocationGroupAssignment, GrazingSession
from app.services.grazing_history_service import GrazingHistoryService
from app.services.validation_service import ValidationService


class GrazingService:
    @staticmethod
    def open_session(farm_id: str, mob_id: str, start_at, allocations: list[dict]):
        ValidationService.validate_allocations(allocations)

        current = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).first()
        if current:
            raise ValueError("Mob already has an active grazing session")

        session = GrazingSession(farm_id=farm_id, mob_id=mob_id, start_at=start_at)
        db.session.add(session)
        db.session.flush()

        for item in allocations:
            allocation = GrazingAllocation(
                grazing_session_id=session.id,
                paddock_id=item["paddock_id"],
                allocation_fraction=Decimal(str(item["allocation_fraction"])),
            )
            db.session.add(allocation)
            db.session.flush()
            for group_count in item.get("group_counts", []):
                db.session.add(
                    GrazingAllocationGroupAssignment(
                        grazing_allocation_id=allocation.id,
                        animal_group_type_id=group_count["animal_group_type_id"],
                        head_count=int(group_count["head_count"]),
                        group_fraction=Decimal(str(group_count["group_fraction"])),
                        assigned_lsu=Decimal(str(group_count["assigned_lsu"])),
                    )
                )

        db.session.flush()
        GrazingHistoryService.sync_live_history_for_mob(mob_id, effective_at=start_at)

        return session

    @staticmethod
    def close_open_session(mob_id: str, end_at=None):
        end_at = end_at or datetime.now(timezone.utc)
        current = GrazingSession.query.filter_by(mob_id=mob_id, end_at=None).first()
        if current:
            current.end_at = end_at
            GrazingHistoryService.close_open_history_for_session(current, end_at=end_at)
        return current
