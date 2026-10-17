from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, CashTransactionLine, Farm, LivestockTrade
from app.services.livestock_trade_service import LivestockTradeService

PREVIOUS = "fa2b4c6d8e10"
CURRENT = "b4c6d8e0f213"


def table(name):
    return sa.Table(name, sa.MetaData(), autoload_with=db.engine)


@pytest.mark.parametrize("populated", [False, True])
def test_trade_migration_preserves_records_and_cashflow_and_roundtrips(tmp_path, populated):
    class MigrationConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "trades.sqlite").as_posix()

    application = create_app(MigrationConfig)
    with application.app_context():
        upgrade(revision=PREVIOUS)
        old_weights = []
        old_trades = []
        old_farms = []
        if populated:
            farm = Farm(name="Migration trade farm")
            cow = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="calf")
            goat = AnimalGroupType(species="Goat", breed="Angora", sex="ewe", age_class="adult")
            db.session.add_all([farm, cow, goat])
            db.session.flush()
            for index, kind in enumerate((cow, goat)):
                transaction = CashTransaction(id=f"cash-{index}", transaction_date=date(2026, 9, 30), counterparty="Buyer", description="Original")
                transaction.lines = [CashTransactionLine(category_code="calf_sales" if index == 0 else "goat_sales", direction="inflow", species_scope=kind.species,
                    amount=Decimal("690.00") if index == 0 else Decimal("2300.00"), source_type="livestock_trade", source_id=f"trade-{index}")]
                db.session.add(transaction)
                db.session.flush()
                db.session.execute(table("livestock_trades").insert().values(
                    id=f"trade-{index}", trade_date=date(2026, 9, 30), trade_type="sale", counterparty="Buyer", reference="Original ref",
                    vat_rate=Decimal("0.15"), animal_group_type_id=kind.id, pricing_model="weight" if index == 0 else "head",
                    price_per_kg=Decimal("20") if index == 0 else None, price_per_head=Decimal("1000") if index == 1 else None,
                    goat_count=2 if index == 1 else None, hair_lengths="long" if index == 1 else None,
                    notes="Preserve me", cash_transaction_id=transaction.id,
                ))
                db.session.execute(table("livestock_trade_farms").insert().values(id=f"link-{index}", livestock_trade_id=f"trade-{index}", farm_id=farm.id, sort_order=0))
            db.session.execute(table("livestock_trade_weights").insert(), [
                {"id": "weight-0", "livestock_trade_id": "trade-0", "sequence": 0, "weight_kg": Decimal("10.125")},
                {"id": "weight-1", "livestock_trade_id": "trade-0", "sequence": 1, "weight_kg": Decimal("19.875")},
            ])
            db.session.commit()
            old_weights = [dict(row) for row in db.session.execute(sa.select(table("livestock_trade_weights"))).mappings()]
            old_trades = [dict(row) for row in db.session.execute(sa.select(table("livestock_trades"))).mappings()]
            old_farms = [dict(row) for row in db.session.execute(sa.select(table("livestock_trade_farms"))).mappings()]
        db.session.remove()
        upgrade(revision=CURRENT)
        assert db.session.execute(sa.text("PRAGMA foreign_key_check")).all() == []
        if populated:
            trades = LivestockTradeService.trade_query().order_by(LivestockTrade.id).all()
            assert [len(trade.groups) for trade in trades] == [1, 1]
            assert trades[0].groups[0].weights[0].id == "weight-0"
            assert trades[0].groups[0].weights[0].weight_kg == Decimal("10.125")
            assert trades[1].groups[0].head_count == 2
            assert trades[1].groups[0].hair_lengths == "long"
            assert [trade.notes for trade in trades] == ["Preserve me"] * 2
            assert [trade.cash_transaction_id for trade in trades] == ["cash-0", "cash-1"]
            assert [trade.cash_transaction.description for trade in trades] == ["Original"] * 2
            assert [trade.cash_transaction.lines[0].amount for trade in trades] == [Decimal("690.00"), Decimal("2300.00")]
            assert [LivestockTradeService.trade_statistics(trade)["gross_total"] for trade in trades] == [Decimal("690.00"), Decimal("2300.00")]
        db.session.remove()
        downgrade(revision=PREVIOUS)
        for name, expected in (("livestock_trade_weights", old_weights), ("livestock_trades", old_trades), ("livestock_trade_farms", old_farms)):
            actual = [dict(row) for row in db.session.execute(sa.select(table(name))).mappings()]
            assert sorted(actual, key=lambda row: row["id"]) == sorted(expected, key=lambda row: row["id"])
        db.session.remove()
        upgrade(revision=CURRENT)
        if populated:
            trade = db.session.get(LivestockTrade, "trade-0")
            trade.import_id = "33a00000-0000-0000-0000-000000000001"
            db.session.commit()
            db.session.remove()
            # Flask-Migrate translates migration errors into a failing CLI exit.
            with pytest.raises(SystemExit) as rejected:
                downgrade(revision=PREVIOUS)
            assert rejected.value.code == 1
            db.session.rollback()
            assert db.session.get(LivestockTrade, "trade-0").import_id is not None
            assert CashTransaction.query.count() == 2
        db.session.remove()
        db.engine.dispose()
