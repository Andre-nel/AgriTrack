from pathlib import Path

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    AnimalGroupBalance,
    CalendarActivity,
    CalendarActivityException,
    CashTransaction,
    DailyStockSnapshot,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    GrazingSession,
    JournalEntry,
    Mob,
    MobEvent,
    MobLineage,
    MovementEvent,
    MovementEventMob,
    NoteAttachment,
    Paddock,
    PaddockEvent,
    PaddockGate,
    RainfallRecord,
    StockLedgerEntry,
    Task,
    TaskComment,
    TaskEntityLink,
    TaskLink,
    TaskSpace,
    TaskSpaceComment,
    TaskStatusTransition,
    UserFarmRole,
    WaterAsset,
    WaterAssetEvent,
    WaterAssetServedPaddock,
    WaterConnection,
)


class FarmDeletionService:
    @staticmethod
    def _ids_for(model, column, value: str) -> list[str]:
        return [row[0] for row in db.session.query(model.id).filter(column == value).all()]

    @staticmethod
    def _delete_where(model, *conditions) -> int:
        filters = [condition for condition in conditions if condition is not None]
        if not filters:
            return 0
        return model.query.filter(or_(*filters)).delete(synchronize_session=False)

    @staticmethod
    def _in_if_any(column, values: list[str]):
        if not values:
            return None
        return column.in_(values)

    @classmethod
    def map_path_for_farm(cls, farm_name: str, instance_path: str | Path) -> Path:
        return Path(instance_path) / "maps" / f"{farm_name}.kml"

    @classmethod
    def delete_map_file(cls, farm_name: str, instance_path: str | Path) -> bool:
        map_path = cls.map_path_for_farm(farm_name, instance_path)
        maps_dir = (Path(instance_path) / "maps").resolve()
        resolved_path = map_path.resolve()
        if maps_dir not in (resolved_path, *resolved_path.parents):
            raise ValueError("Farm map path is outside the maps directory")
        if not resolved_path.exists():
            return False
        resolved_path.unlink()
        return True

    @classmethod
    def delete_farm_records(cls, farm: Farm) -> None:
        farm_id = str(farm.id)
        mob_ids = cls._ids_for(Mob, Mob.farm_id, farm_id)
        paddock_ids = cls._ids_for(Paddock, Paddock.farm_id, farm_id)
        movement_event_ids = cls._ids_for(MovementEvent, MovementEvent.farm_id, farm_id)
        grazing_session_ids = [
            row[0]
            for row in db.session.query(GrazingSession.id)
            .filter(
                or_(
                    GrazingSession.farm_id == farm_id,
                    cls._in_if_any(GrazingSession.mob_id, mob_ids),
                )
            )
            .all()
        ]
        grazing_allocation_ids = [
            row[0]
            for row in db.session.query(GrazingAllocation.id)
            .filter(
                or_(
                    cls._in_if_any(GrazingAllocation.grazing_session_id, grazing_session_ids),
                    cls._in_if_any(GrazingAllocation.paddock_id, paddock_ids),
                )
            )
            .all()
        ]
        water_asset_ids = [
            row[0]
            for row in db.session.query(WaterAsset.id)
            .filter(
                or_(
                    WaterAsset.farm_id == farm_id,
                    cls._in_if_any(WaterAsset.location_paddock_id, paddock_ids),
                )
            )
            .all()
        ]
        calendar_activity_ids = cls._ids_for(CalendarActivity, CalendarActivity.farm_id, farm_id)
        task_space_ids = cls._ids_for(TaskSpace, TaskSpace.farm_id, farm_id)
        task_ids = [
            row[0]
            for row in db.session.query(Task.id)
            .filter(cls._in_if_any(Task.space_id, task_space_ids))
            .all()
        ]

        cls._delete_where(
            TaskEntityLink,
            cls._in_if_any(TaskEntityLink.task_id, task_ids),
            cls._in_if_any(TaskEntityLink.paddock_id, paddock_ids),
            cls._in_if_any(TaskEntityLink.water_asset_id, water_asset_ids),
            cls._in_if_any(TaskEntityLink.mob_id, mob_ids),
        )
        cls._delete_where(
            TaskLink,
            cls._in_if_any(TaskLink.source_task_id, task_ids),
            cls._in_if_any(TaskLink.target_task_id, task_ids),
            cls._in_if_any(TaskLink.source_space_id, task_space_ids),
            cls._in_if_any(TaskLink.target_space_id, task_space_ids),
        )
        cls._delete_where(TaskStatusTransition, cls._in_if_any(TaskStatusTransition.task_id, task_ids))
        cls._delete_where(TaskComment, cls._in_if_any(TaskComment.task_id, task_ids))
        cls._delete_where(TaskSpaceComment, cls._in_if_any(TaskSpaceComment.space_id, task_space_ids))
        cls._delete_where(Task, cls._in_if_any(Task.id, task_ids))
        cls._delete_where(TaskSpace, cls._in_if_any(TaskSpace.id, task_space_ids))

        cls._delete_where(NoteAttachment, NoteAttachment.farm_id == farm_id)
        cls._delete_where(
            WaterAssetEvent,
            WaterAssetEvent.farm_id == farm_id,
            cls._in_if_any(WaterAssetEvent.water_asset_id, water_asset_ids),
        )
        cls._delete_where(
            WaterConnection,
            WaterConnection.farm_id == farm_id,
            cls._in_if_any(WaterConnection.source_asset_id, water_asset_ids),
            cls._in_if_any(WaterConnection.destination_asset_id, water_asset_ids),
            cls._in_if_any(WaterConnection.pump_asset_id, water_asset_ids),
        )
        cls._delete_where(
            WaterAssetServedPaddock,
            cls._in_if_any(WaterAssetServedPaddock.water_asset_id, water_asset_ids),
            cls._in_if_any(WaterAssetServedPaddock.paddock_id, paddock_ids),
        )
        cls._delete_where(WaterAsset, cls._in_if_any(WaterAsset.id, water_asset_ids))

        cls._delete_where(
            GrazingAllocationLsuHistory,
            GrazingAllocationLsuHistory.farm_id == farm_id,
            cls._in_if_any(GrazingAllocationLsuHistory.mob_id, mob_ids),
            cls._in_if_any(GrazingAllocationLsuHistory.paddock_id, paddock_ids),
            cls._in_if_any(GrazingAllocationLsuHistory.grazing_session_id, grazing_session_ids),
            cls._in_if_any(GrazingAllocationLsuHistory.grazing_allocation_id, grazing_allocation_ids),
        )
        cls._delete_where(
            GrazingAllocation,
            cls._in_if_any(GrazingAllocation.id, grazing_allocation_ids),
            cls._in_if_any(GrazingAllocation.grazing_session_id, grazing_session_ids),
            cls._in_if_any(GrazingAllocation.paddock_id, paddock_ids),
        )
        cls._delete_where(
            GrazingSession,
            GrazingSession.farm_id == farm_id,
            cls._in_if_any(GrazingSession.id, grazing_session_ids),
            cls._in_if_any(GrazingSession.mob_id, mob_ids),
        )

        cls._delete_where(MobLineage, cls._in_if_any(MobLineage.movement_event_id, movement_event_ids))
        cls._delete_where(
            MobLineage,
            cls._in_if_any(MobLineage.parent_mob_id, mob_ids),
            cls._in_if_any(MobLineage.child_mob_id, mob_ids),
        )
        cls._delete_where(
            MovementEventMob,
            cls._in_if_any(MovementEventMob.movement_event_id, movement_event_ids),
            cls._in_if_any(MovementEventMob.mob_id, mob_ids),
        )
        cls._delete_where(MovementEvent, MovementEvent.farm_id == farm_id)

        cls._delete_where(StockLedgerEntry, StockLedgerEntry.farm_id == farm_id)
        cls._delete_where(StockLedgerEntry, cls._in_if_any(StockLedgerEntry.mob_id, mob_ids))
        cls._delete_where(AnimalGroupBalance, cls._in_if_any(AnimalGroupBalance.mob_id, mob_ids))
        cls._delete_where(
            MobEvent,
            MobEvent.farm_id == farm_id,
            cls._in_if_any(MobEvent.mob_id, mob_ids),
        )
        cls._delete_where(
            PaddockEvent,
            PaddockEvent.farm_id == farm_id,
            cls._in_if_any(PaddockEvent.paddock_id, paddock_ids),
        )
        cls._delete_where(
            PaddockGate,
            PaddockGate.farm_id == farm_id,
            cls._in_if_any(PaddockGate.paddock_a_id, paddock_ids),
            cls._in_if_any(PaddockGate.paddock_b_id, paddock_ids),
        )
        cls._delete_where(DailyStockSnapshot, DailyStockSnapshot.farm_id == farm_id)
        cls._delete_where(DailyStockSnapshot, cls._in_if_any(DailyStockSnapshot.paddock_id, paddock_ids))
        cls._delete_where(RainfallRecord, RainfallRecord.farm_id == farm_id)
        cls._delete_where(JournalEntry, JournalEntry.farm_id == farm_id)
        cls._delete_where(
            CalendarActivityException,
            cls._in_if_any(CalendarActivityException.calendar_activity_id, calendar_activity_ids),
        )
        cls._delete_where(CalendarActivity, CalendarActivity.farm_id == farm_id)
        cls._delete_where(UserFarmRole, UserFarmRole.farm_id == farm_id)

        CashTransaction.query.filter(CashTransaction.farm_id == farm_id).update(
            {"farm_id": None},
            synchronize_session=False,
        )

        cls._delete_where(Paddock, cls._in_if_any(Paddock.id, paddock_ids))
        cls._delete_where(Mob, cls._in_if_any(Mob.id, mob_ids))
        db.session.delete(farm)
