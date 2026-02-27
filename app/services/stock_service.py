from datetime import datetime, timezone

from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, StockLedgerEntry
from app.models.stock_ledger import StockEventType
from app.services.validation_service import ValidationService

IN_EVENTS = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}


class StockService:
    @staticmethod
    def get_or_create_group_type(species: str, breed: str, sex: str, age_class: str) -> AnimalGroupType:
        group_type = AnimalGroupType.query.filter_by(
            species=species,
            breed=breed,
            sex=sex,
            age_class=age_class,
        ).first()
        if group_type:
            return group_type

        group_type = AnimalGroupType(species=species, breed=breed, sex=sex, age_class=age_class)
        db.session.add(group_type)
        db.session.flush()
        return group_type

    @staticmethod
    def adjust_stock(
        mob_id: str,
        farm_id: str,
        animal_group_type_id: str,
        event_type: StockEventType,
        quantity: int,
        note: str | None = None,
    ) -> StockLedgerEntry:
        ValidationService.validate_positive_int(quantity, "quantity")

        ledger = StockLedgerEntry(
            mob_id=mob_id,
            farm_id=farm_id,
            animal_group_type_id=animal_group_type_id,
            event_type=event_type,
            quantity=quantity,
            event_time=datetime.now(timezone.utc),
            note=note,
        )
        db.session.add(ledger)

        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob_id, animal_group_type_id=animal_group_type_id
        ).first()
        if not balance:
            balance = AnimalGroupBalance(
                mob_id=mob_id,
                animal_group_type_id=animal_group_type_id,
                head_count=0,
            )
            db.session.add(balance)

        delta = quantity if event_type in IN_EVENTS else -quantity
        next_balance = balance.head_count + delta
        if next_balance < 0:
            raise ValueError("Stock balance cannot go negative")

        balance.head_count = next_balance
        return ledger
