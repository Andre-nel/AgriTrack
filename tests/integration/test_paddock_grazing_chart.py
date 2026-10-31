import json
import re
from datetime import datetime

from app.extensions import db
from app.models import Farm, GrazingAllocation, GrazingSession, Mob, Paddock
from app.services.reporting_service import ReportingService


def test_grazing_chart_matches_table_in_chronological_order(client, app, monkeypatch):
    monkeypatch.setattr(ReportingService, "allocation_lsu", lambda allocation: 12.5)
    with app.app_context():
        farm = Farm(name="Chart Farm", timezone="SAST", default_stocking_rate_ha_per_lsu=6)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="Chart Camp", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Mob </script> & cattle", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()
        for start, end in [(datetime(2026, 2, 1), None), (datetime(2026, 1, 1), datetime(2026, 1, 3))]:
            session = GrazingSession(farm_id=farm.id, mob_id=mob.id, start_at=start, end_at=end)
            db.session.add(session)
            db.session.flush()
            db.session.add(GrazingAllocation(grazing_session_id=session.id, paddock_id=paddock.id, allocation_fraction=0.5))
        db.session.commit()
        paddock_id = paddock.id
    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    payload = json.loads(re.search(r'<script id="paddockGrazingHistoryData" type="application/json">(.*?)</script>', body).group(1))
    assert [row["start"] for row in payload] == ["2026-01-01 00:00:00", "2026-02-01 00:00:00"]
    assert payload[0]["end"] == "2026-01-03 00:00:00"
    assert payload[1]["end"] is None
    assert all(row["allocated_lsu"] == 12.5 and row["allocation_pct"] == 50 for row in payload)
    assert payload[0]["mob_name"] == "Mob </script> & cattle"
    assert ">12.50<" in body


def test_empty_grazing_history_does_not_load_chart(client, app):
    with app.app_context():
        farm = Farm(name="Empty Farm", timezone="SAST", default_stocking_rate_ha_per_lsu=6)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="Empty Camp", area_ha=10, grazeable_area_ha=10)
        db.session.add(paddock)
        db.session.commit()
        paddock_id = paddock.id
    response = client.get(f"/paddocks/{paddock_id}")
    assert response.status_code == 200
    assert b"No grazing history." in response.data
    assert b"paddockGrazingHistoryChart" not in response.data
    assert b"chart.umd.min.js" not in response.data
