from sqlalchemy import func

from app.models import AnimalGroupBalance, StockLedgerEntry


class StockRepository:
    @staticmethod
    def balances_for_mob(mob_id: str):
        return AnimalGroupBalance.query.filter_by(mob_id=mob_id).all()

    @staticmethod
    def ledger_for_mob(mob_id: str, limit: int = 100):
        return (
            StockLedgerEntry.query.filter_by(mob_id=mob_id)
            .order_by(StockLedgerEntry.event_time.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def total_by_group_for_farm(farm_id: str):
        return (
            AnimalGroupBalance.query.join(AnimalGroupBalance.mob)
            .with_entities(
                AnimalGroupBalance.animal_group_type_id,
                func.sum(AnimalGroupBalance.head_count).label("head_count"),
            )
            .filter_by(farm_id=farm_id)
            .group_by(AnimalGroupBalance.animal_group_type_id)
            .all()
        )
