"""Recorded inventory ownership and the reconciliation anchor for LSU history."""

from app.extensions import db


class MobInventoryStateHistory(db.Model):
    __tablename__ = "mob_inventory_state_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    mob_id = db.Column(
        db.String(36), db.ForeignKey("mobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    farm_id = db.Column(
        db.String(36), db.ForeignKey("farms.id", ondelete="SET NULL"), nullable=True, index=True
    )
    previous_farm_id = db.Column(
        db.String(36), db.ForeignKey("farms.id", ondelete="SET NULL"), nullable=True
    )
    active = db.Column(db.Boolean, nullable=False)
    previous_active = db.Column(db.Boolean, nullable=True)
    effective_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False)
    source = db.Column(db.String(20), nullable=False)

    baseline_groups = db.relationship(
        "MobInventoryBaselineGroup", back_populates="state", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.CheckConstraint(
            "source IN ('baseline', 'created', 'move', 'status')",
            name="ck_inventory_state_source",
        ),
        db.Index(
            "uq_inventory_initial_state", "mob_id", unique=True,
            sqlite_where=db.text("source IN ('baseline', 'created')"),
            postgresql_where=db.text("source IN ('baseline', 'created')"),
        ),
        {"sqlite_autoincrement": True},
    )


class MobInventoryBaselineGroup(db.Model):
    __tablename__ = "mob_inventory_baseline_groups"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    state_id = db.Column(
        db.Integer, db.ForeignKey("mob_inventory_state_history.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    animal_group_type_id = db.Column(
        db.String(36), db.ForeignKey("animal_group_types.id"), nullable=False
    )
    head_count = db.Column(db.Integer, nullable=False)
    ledger_net = db.Column(db.Integer, nullable=False)

    state = db.relationship("MobInventoryStateHistory", back_populates="baseline_groups")

    __table_args__ = (
        db.CheckConstraint("head_count >= 0", name="ck_inventory_baseline_head_count"),
        db.UniqueConstraint("state_id", "animal_group_type_id", name="uq_inventory_baseline_group"),
    )
