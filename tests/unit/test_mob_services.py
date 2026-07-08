from app.extensions import db
from app.models import AnimalGroupBalance, AnimalGroupType, Farm, Mob, MobEvent, StockLedgerEntry
from app.modules.mobs.services import adjust_mob_stock_from_form, update_mob_balance_line_from_form


def _create_mob_with_balance(head_count: int = 5):
    farm = Farm(name="Service Farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    mob = Mob(farm_id=farm.id, name="Service Mob", status="active")
    group_type = AnimalGroupType(
        species="Sheep",
        breed="Merino",
        sex="ewe",
        age_class="adult",
    )
    db.session.add_all([mob, group_type])
    db.session.flush()
    balance = AnimalGroupBalance(
        mob_id=mob.id,
        animal_group_type_id=group_type.id,
        head_count=head_count,
    )
    db.session.add(balance)
    db.session.commit()
    return mob, group_type


def test_adjust_mob_stock_from_form_count_match_posts_no_ledger(app):
    with app.app_context():
        mob, _group_type = _create_mob_with_balance(head_count=5)

        message = adjust_mob_stock_from_form(
            mob,
            {
                "species": "Sheep",
                "breed": "Merino",
                "sex": "ewe",
                "age_class": "adult",
                "event_type": "count",
                "quantity": "5",
            },
        )

        assert message == "Count matches current balance. No stock adjustment posted."
        assert StockLedgerEntry.query.count() == 0


def test_adjust_mob_stock_from_form_count_posts_delta_and_reports_resolution(app):
    with app.app_context():
        mob, group_type = _create_mob_with_balance(head_count=2)

        message = adjust_mob_stock_from_form(
            mob,
            {
                "species": "Sheep",
                "breed": "Merino",
                "sex": "ewe",
                "age_class": "adult",
                "event_type": "count",
                "quantity": "10",
            },
        )

        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob.id,
            animal_group_type_id=group_type.id,
        ).first()
        ledger = StockLedgerEntry.query.filter_by(
            mob_id=mob.id,
            animal_group_type_id=group_type.id,
        ).first()

        assert message == "Count set to 10. Posted adjustment_in of 8."
        assert balance is not None
        assert balance.head_count == 10
        assert ledger is not None
        assert ledger.event_type.value == "adjustment_in"
        assert ledger.quantity == 8


def test_update_mob_balance_line_from_form_updates_balance_and_records_event(app):
    with app.app_context():
        mob, group_type = _create_mob_with_balance(head_count=5)

        message = update_mob_balance_line_from_form(
            mob,
            {
                "source_animal_group_type_id": str(group_type.id),
                "head_count": "7",
                "sex": "ewe",
                "age_class": "adult",
                "note": "body condition improved",
            },
        )

        balance = AnimalGroupBalance.query.filter_by(
            mob_id=mob.id,
            animal_group_type_id=group_type.id,
        ).first()
        event = MobEvent.query.filter_by(mob_id=mob.id).first()

        assert message == "Balance line updated"
        assert balance.head_count == 7
        assert StockLedgerEntry.query.count() == 1
        assert event is not None
        assert "body condition improved" in event.description
