from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AnimalGroupType, CashTransaction, CashTransactionLine, Farm
from app.models import LivestockTrade, LivestockTradeAttachment, LivestockTradeGroup


PREVIOUS = "b4c6d8e0f213"
CURRENT = "c6d8e0f213a5"


def table(name):
    return sa.Table(name, sa.MetaData(), autoload_with=db.engine)


def snapshot():
    return {
        name: [dict(row) for row in db.session.execute(sa.select(table(name))).mappings()]
        for name in ("livestock_trades", "livestock_trade_groups", "cash_transactions", "cash_transaction_lines")
    }


@pytest.mark.parametrize("populated", [False, True])
def test_image_migration_preserves_existing_trades_and_cashflow(tmp_path, populated):
    class MigrationConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "trade-images.sqlite").as_posix()

    application = create_app(MigrationConfig)
    with application.app_context():
        upgrade(revision=PREVIOUS)
        if populated:
            farm = Farm(name="Existing image migration farm")
            animal_type = AnimalGroupType(species="Cattle", breed="Bonsmara", sex="cow", age_class="young")
            transaction = CashTransaction(
                transaction_date=date(2026, 10, 15), counterparty="Existing buyer", description="Existing trade",
            )
            transaction.lines = [CashTransactionLine(
                category_code="calf_sales", direction="inflow", species_scope="Cattle", amount=Decimal("460.00"),
            )]
            db.session.add_all([farm, animal_type, transaction])
            db.session.flush()
            trade = LivestockTrade(
                trade_date=date(2026, 10, 15), trade_type="sale", counterparty="Existing buyer",
                species="Cattle", pricing_model="head", vat_rate=Decimal("0.15"),
                cash_transaction_id=transaction.id,
            )
            trade.groups = [LivestockTradeGroup(
                sort_order=0, animal_group_type=animal_type, price_per_head=Decimal("200.00"), head_count=2,
            )]
            db.session.add(trade)
            db.session.commit()
        before = snapshot()
        db.session.remove()
        upgrade(revision=CURRENT)
        assert "livestock_trade_attachments" in sa.inspect(db.engine).get_table_names()
        assert LivestockTradeAttachment.query.count() == 0
        assert db.session.execute(sa.text("PRAGMA foreign_key_check")).all() == []
        assert snapshot() == before
        db.session.remove()
        downgrade(revision=PREVIOUS)
        assert "livestock_trade_attachments" not in sa.inspect(db.engine).get_table_names()
        assert snapshot() == before
        db.session.remove()
        upgrade(revision=CURRENT)
        assert snapshot() == before
        if populated:
            trade = LivestockTrade.query.one()
            attachment = LivestockTradeAttachment(
                livestock_trade_id=trade.id, sort_order=0, original_filename="reference.png",
                content_type="image/png", byte_size=128, sha256="a" * 64,
                storage_path="livestock_trade_attachments/existing/reference.png",
            )
            db.session.add(attachment)
            db.session.commit()
            db.session.remove()
            with pytest.raises(SystemExit) as rejected:
                downgrade(revision=PREVIOUS)
            assert rejected.value.code == 1
            db.session.rollback()
            assert LivestockTradeAttachment.query.one().original_filename == "reference.png"
            assert snapshot() == before
        db.session.remove()
        db.engine.dispose()
