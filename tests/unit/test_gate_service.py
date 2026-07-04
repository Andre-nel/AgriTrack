from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Farm, GrazingSession, Mob, Paddock
from app.services.gate_service import GateService
from app.services.movement_service import MovementService


def _square(lon, lat, size=0.001):
    return [
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    ]


def _farm_with_paddocks(*areas):
    farm = Farm(name="Gate Farm", timezone="UTC", active=True)
    db.session.add(farm)
    db.session.flush()
    paddocks = []
    for index, (name, area, grazeable) in enumerate(areas, start=1):
        paddock = Paddock(
            farm_id=farm.id,
            name=name or f"Camp {index}",
            area_ha=area,
            grazeable_area_ha=grazeable,
            status="active",
        )
        db.session.add(paddock)
        paddocks.append(paddock)
    db.session.flush()
    return farm, paddocks


def _mob_in_allocations(farm, allocations):
    mob = Mob(farm_id=farm.id, name=f"Mob {Mob.query.count() + 1}", status="active")
    db.session.add(mob)
    db.session.flush()
    MovementService.move_mob(
        mob=mob,
        allocations=[
            {"paddock_id": paddock.id, "allocation_fraction": str(fraction)}
            for paddock, fraction in allocations
        ],
        destination_farm_id=str(farm.id),
        when=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.session.flush()
    return mob


def _allocation_map(mob):
    session = GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()
    return {
        str(allocation.paddock_id): Decimal(str(allocation.allocation_fraction))
        for allocation in session.allocations
    }


def _active_session(mob):
    return GrazingSession.query.filter_by(mob_id=mob.id, end_at=None).one()


def _naive_utc(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def test_detect_shared_boundaries_handles_reversed_edges_and_ignores_non_adjacent():
    left = SimpleNamespace(id="left", name="Left")
    right = SimpleNamespace(id="right", name="Right")
    away = SimpleNamespace(id="away", name="Away")

    detections = GateService.detect_shared_boundaries(
        [
            {"paddock": left, "rings": [_square(0.0, 0.0)]},
            {"paddock": right, "rings": [list(reversed(_square(0.001, 0.0)))]},
            {"paddock": away, "rings": [_square(0.004, 0.0)]},
        ]
    )

    assert len(detections) == 1
    detection = detections[0]
    assert detection["paddock_a"] is left
    assert detection["paddock_b"] is right
    assert detection["shared_boundary_length_m"] > 100
    assert detection["longitude"] == pytest.approx(0.001, abs=0.000001)
    assert detection["latitude"] == pytest.approx(0.0005, abs=0.000001)


def test_open_gate_redistributes_active_mob_by_grazeable_area_and_records_time(app):
    farm, (north, south) = _farm_with_paddocks(("North", 10, 10), ("South", 30, 30))
    mob = _mob_in_allocations(farm, [(north, Decimal("1.0"))])
    gate = GateService.create_manual_gate(
        farm_id=str(farm.id),
        paddock_a_id=str(north.id),
        paddock_b_id=str(south.id),
    )
    db.session.commit()

    event_time = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    result = GateService.set_gate_state(gate, "open", event_time=event_time)
    db.session.commit()

    assert result["moved_mob_count"] == 1
    assert _allocation_map(mob) == {
        str(north.id): Decimal("0.2500"),
        str(south.id): Decimal("0.7500"),
    }
    assert _active_session(mob).start_at == _naive_utc(event_time)
    closed_sessions = GrazingSession.query.filter(
        GrazingSession.mob_id == mob.id,
        GrazingSession.end_at == _naive_utc(event_time),
    ).all()
    assert len(closed_sessions) == 1


def test_open_gate_network_adjusts_new_percentage_move_allocations(app):
    farm, (north, south) = _farm_with_paddocks(("North", 10, 10), ("South", 30, 30))
    gate = GateService.create_manual_gate(
        farm_id=str(farm.id),
        paddock_a_id=str(north.id),
        paddock_b_id=str(south.id),
    )
    gate.status = "open"
    db.session.commit()

    allocations = GateService.allocations_for_open_gate_network(
        str(farm.id),
        [{"paddock_id": str(south.id), "allocation_fraction": "1.0"}],
    )

    assert allocations == [
        {"paddock_id": str(north.id), "allocation_fraction": "0.2500"},
        {"paddock_id": str(south.id), "allocation_fraction": "0.7500"},
    ]


def test_open_gate_chain_redistributes_across_full_connected_component(app):
    farm, (a, b, c) = _farm_with_paddocks(("A", 10, 10), ("B", 10, 10), ("C", 20, 20))
    mob = _mob_in_allocations(farm, [(a, Decimal("1.0"))])
    gate_ab = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(a.id), paddock_b_id=str(b.id))
    gate_bc = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(b.id), paddock_b_id=str(c.id))
    db.session.commit()

    GateService.set_gate_state(gate_ab, "open", event_time=datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc))
    db.session.commit()
    GateService.set_gate_state(gate_bc, "open", event_time=datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc))
    db.session.commit()

    assert _allocation_map(mob) == {
        str(a.id): Decimal("0.2500"),
        str(b.id): Decimal("0.2500"),
        str(c.id): Decimal("0.5000"),
    }


def test_open_gate_preserves_unrelated_outside_allocations(app):
    farm, (a, b, outside) = _farm_with_paddocks(
        ("A", 10, 10),
        ("B", 10, 10),
        ("Outside", 50, 50),
    )
    mob = _mob_in_allocations(farm, [(a, Decimal("0.6")), (outside, Decimal("0.4"))])
    gate = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(a.id), paddock_b_id=str(b.id))
    db.session.commit()

    GateService.set_gate_state(gate, "open", event_time=datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc))
    db.session.commit()

    assert _allocation_map(mob) == {
        str(a.id): Decimal("0.3000"),
        str(b.id): Decimal("0.3000"),
        str(outside.id): Decimal("0.4000"),
    }


def test_zero_area_component_splits_equally(app):
    farm, (a, b) = _farm_with_paddocks(("A", 0, 0), ("B", 0, 0))
    mob = _mob_in_allocations(farm, [(a, Decimal("1.0"))])
    gate = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(a.id), paddock_b_id=str(b.id))
    db.session.commit()

    GateService.set_gate_state(gate, "open", event_time=datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc))
    db.session.commit()

    assert _allocation_map(mob) == {
        str(a.id): Decimal("0.5000"),
        str(b.id): Decimal("0.5000"),
    }


def test_close_gate_requires_choices_for_mobs_spread_across_split_components(app):
    farm, (a, b, c) = _farm_with_paddocks(("A", 10, 10), ("B", 10, 10), ("C", 20, 20))
    mob = _mob_in_allocations(farm, [(a, Decimal("1.0"))])
    gate_ab = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(a.id), paddock_b_id=str(b.id))
    gate_bc = GateService.create_manual_gate(farm_id=str(farm.id), paddock_a_id=str(b.id), paddock_b_id=str(c.id))
    db.session.commit()
    GateService.set_gate_state(gate_ab, "open", event_time=datetime(2026, 6, 12, 8, 0, tzinfo=timezone.utc))
    db.session.commit()
    GateService.set_gate_state(gate_bc, "open", event_time=datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc))
    db.session.commit()

    with pytest.raises(ValueError, match="Choose a closing-side camp"):
        GateService.set_gate_state(
            gate_bc,
            "closed",
            event_time=datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
        )

    result = GateService.set_gate_state(
        gate_bc,
        "closed",
        event_time=datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
        closure_choices=[{"mob_id": str(mob.id), "component_paddock_id": str(a.id)}],
    )
    db.session.commit()

    assert result["moved_mob_count"] == 1
    assert _allocation_map(mob) == {
        str(a.id): Decimal("0.5000"),
        str(b.id): Decimal("0.5000"),
    }
