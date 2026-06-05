from datetime import date, datetime, timezone

import pytest

from app.extensions import db
from app.models import (
    AnimalGroupType,
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuBreakdownHistory,
    GrazingSession,
    JournalEntry,
    Mob,
    Paddock,
    WaterAsset,
    WaterAssetStateHistory,
)
from app.modules.analytics.services import (
    build_lsu_paddock_tracking_report,
    build_water_asset_state_report,
    create_journal_entry,
)


def test_create_journal_entry_normalizes_tags_and_commits(app):
    with app.app_context():
        farm = Farm(name="Journal Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.commit()

        entry = create_journal_entry(
            farm_id=str(farm.id),
            tags_raw="Health; health; Stock",
            description="  Checked mob condition. ",
            event_at_raw="2026-03-05T09:30:00",
        )

        saved = JournalEntry.query.filter_by(id=entry.id).first()
        assert saved is not None
        assert saved.farm_id == str(farm.id)
        assert saved.tags_csv == "health,stock"
        assert saved.description == "Checked mob condition."
        assert saved.event_at.isoformat() == "2026-03-05T09:30:00"


def test_create_journal_entry_rejects_missing_farm(app):
    with app.app_context():
        with pytest.raises(ValueError, match="Journal entry farm is required"):
            create_journal_entry(
                farm_id="missing",
                tags_raw="health",
                description="Checked mob condition.",
                event_at_raw="2026-03-05T09:30:00",
            )


def test_build_water_asset_state_report_carries_baseline_and_summarizes(app):
    with app.app_context():
        farm = Farm(name="Water Analytics Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        tank = WaterAsset(
            farm_id=farm.id,
            name="History Tank",
            asset_type="tank",
            active=True,
            status="leaking",
            water_level="half",
        )
        trough = WaterAsset(
            farm_id=farm.id,
            name="History Trough",
            asset_type="trough",
            active=True,
            status="operational",
            water_level="full",
        )
        db.session.add_all([tank, trough])
        db.session.flush()
        db.session.add_all(
            [
                WaterAssetStateHistory(
                    water_asset_id=tank.id,
                    farm_id=farm.id,
                    change_type="created",
                    changed_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
                    previous_active=None,
                    previous_status=None,
                    previous_water_level=None,
                    active=True,
                    status="operational",
                    water_level="low",
                ),
                WaterAssetStateHistory(
                    water_asset_id=tank.id,
                    farm_id=farm.id,
                    change_type="updated",
                    changed_at=datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc),
                    previous_active=True,
                    previous_status="operational",
                    previous_water_level="low",
                    active=True,
                    status="operational",
                    water_level="half",
                ),
                WaterAssetStateHistory(
                    water_asset_id=tank.id,
                    farm_id=farm.id,
                    change_type="updated",
                    changed_at=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
                    previous_active=True,
                    previous_status="operational",
                    previous_water_level="half",
                    active=True,
                    status="leaking",
                    water_level="half",
                ),
                WaterAssetStateHistory(
                    water_asset_id=trough.id,
                    farm_id=farm.id,
                    change_type="created",
                    changed_at=datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc),
                    previous_active=None,
                    previous_status=None,
                    previous_water_level=None,
                    active=True,
                    status="operational",
                    water_level="full",
                ),
            ]
        )
        db.session.commit()

        report = build_water_asset_state_report(
            assets=[tank, trough],
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 6),
            state_fields=["active", "water_level"],
            plot_mode="overlay",
            include_farm_name=False,
        )

        assert report["chart_payload"]["labels"] == [
            "2026-01-02",
            "2026-01-03",
            "2026-01-04",
            "2026-01-05",
            "2026-01-06",
        ]
        active_panel = report["chart_payload"]["panels"][0]
        water_level_panel = report["chart_payload"]["panels"][1]
        assert active_panel["title"] == "Active State Over Time"
        assert active_panel["value_labels"] == {"0": "Inactive", "1": "Active"}
        assert active_panel["datasets"] == [
            {"label": "History Tank", "values": [1, 1, 1, 1, 1]},
            {"label": "History Trough", "values": [None, None, 1, 1, 1]},
        ]
        assert water_level_panel["value_labels"] == {
            "0": "Empty",
            "1": "Low",
            "2": "Half",
            "3": "High",
            "4": "Full",
        }
        assert water_level_panel["datasets"] == [
            {"label": "History Tank", "values": [1, 2, 2, 2, 2]},
            {"label": "History Trough", "values": [None, None, 4, 4, 4]},
        ]

        tank_summary = next(row for row in report["summary_rows"] if row["asset_name"] == "History Tank")
        assert tank_summary["history_event_count"] == 2
        assert tank_summary["active_days"] == 5
        assert tank_summary["inactive_days"] == 0
        assert tank_summary["status_change_count"] == 1
        assert tank_summary["water_level_change_count"] == 1
        assert tank_summary["dominant_status_label"] == "Operational"
        assert tank_summary["dominant_water_level_label"] == "Half"
        assert tank_summary["empty_low_days"] == 1
        assert tank_summary["half_or_better_days"] == 4

        trough_summary = next(
            row for row in report["summary_rows"] if row["asset_name"] == "History Trough"
        )
        assert trough_summary["first_observed_date"] == "2026-01-04"
        assert trough_summary["history_event_count"] == 1
        assert trough_summary["active_days"] == 3
        assert trough_summary["half_or_better_days"] == 3


def test_build_water_asset_state_report_supports_asset_plot_mode(app):
    with app.app_context():
        farm = Farm(name="Water Asset Plot Farm", timezone="UTC", active=True)
        db.session.add(farm)
        db.session.flush()
        tank = WaterAsset(
            farm_id=farm.id,
            name="Status Tank",
            asset_type="tank",
            active=True,
            status="leaking",
            water_level="half",
        )
        db.session.add(tank)
        db.session.flush()
        db.session.add_all(
            [
                WaterAssetStateHistory(
                    water_asset_id=tank.id,
                    farm_id=farm.id,
                    change_type="created",
                    changed_at=datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc),
                    active=True,
                    status="operational",
                    water_level="half",
                ),
                WaterAssetStateHistory(
                    water_asset_id=tank.id,
                    farm_id=farm.id,
                    change_type="updated",
                    changed_at=datetime(2026, 2, 3, 8, 0, tzinfo=timezone.utc),
                    previous_active=True,
                    previous_status="operational",
                    previous_water_level="half",
                    active=True,
                    status="leaking",
                    water_level="half",
                ),
            ]
        )
        db.session.commit()

        report = build_water_asset_state_report(
            assets=[tank],
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 3),
            state_fields=["status"],
            plot_mode="asset",
            include_farm_name=False,
        )

        assert report["chart_payload"]["plot_mode"] == "asset"
        assert report["chart_payload"]["panels"] == [
            {
                "id": "status:Status Tank | Status",
                "title": "Status Tank | Status",
                "field": "status",
                "y_axis_label": "Status",
                "y_min": 0,
                "y_max": 1,
                "value_labels": {"0": "Leaking", "1": "Operational"},
                "datasets": [{"label": "Status Tank", "values": [1, 1, 0]}],
            }
        ]


def test_build_lsu_paddock_tracking_report_filters_species_and_summarizes(app):
    with app.app_context():
        farm = Farm(name="LSU Report Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="North Camp", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Mixed Mob", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()
        cattle = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([cattle, sheep])
        db.session.flush()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction=1,
        )
        db.session.add(allocation)
        db.session.flush()
        db.session.add_all(
            [
                GrazingAllocationLsuBreakdownHistory(
                    farm_id=farm.id,
                    mob_id=mob.id,
                    paddock_id=paddock.id,
                    grazing_session_id=session.id,
                    grazing_allocation_id=allocation.id,
                    animal_group_type_id=cattle.id,
                    effective_from=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
                    effective_to=datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc),
                    allocation_fraction=1,
                    head_count=5,
                    group_lsu=5,
                    allocated_lsu=5,
                    source="backfill_ledger",
                ),
                GrazingAllocationLsuBreakdownHistory(
                    farm_id=farm.id,
                    mob_id=mob.id,
                    paddock_id=paddock.id,
                    grazing_session_id=session.id,
                    grazing_allocation_id=allocation.id,
                    animal_group_type_id=sheep.id,
                    effective_from=datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
                    effective_to=datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc),
                    allocation_fraction=1,
                    head_count=6,
                    group_lsu=1,
                    allocated_lsu=1,
                    source="backfill_ledger",
                ),
            ]
        )
        db.session.commit()

        report = build_lsu_paddock_tracking_report(
            paddocks=[paddock],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 3),
            species="Cattle",
            metric="lsu_per_ha",
            plot_mode="overlay",
            group_by_species=False,
            include_farm_name=False,
        )

        assert report["chart_payload"]["labels"] == ["2026-03-01", "2026-03-02", "2026-03-03"]
        assert report["chart_payload"]["panels"][0]["datasets"] == [
            {
                "key": f"{paddock.id}:all",
                "paddock_id": str(paddock.id),
                "label": "North Camp",
                "values": [0.5, 0.5, 0.0],
            }
        ]
        assert report["summary_rows"] == [
            {
                "paddock_name": "North Camp",
                "species": "Cattle",
                "total_lsu_days": 10.0,
                "lsu_days_per_ha": 1.0,
                "average_lsu": 3.33,
                "average_lsu_per_ha": 0.3333,
                "peak_lsu_per_ha": 0.5,
                "grazing_days": 2,
                "rest_days": 1,
                "last_grazed_date": "2026-03-02",
            }
        ]


def test_build_lsu_paddock_tracking_report_groups_by_species(app):
    with app.app_context():
        farm = Farm(name="Species Group Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="West Camp", area_ha=20, grazeable_area_ha=20)
        mob = Mob(farm_id=farm.id, name="Group Mob", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()
        cattle = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        sheep = AnimalGroupType(species="Sheep", breed="Merino", sex="ewe", age_class="adult")
        db.session.add_all([cattle, sheep])
        db.session.flush()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction=1,
        )
        db.session.add(allocation)
        db.session.flush()
        for group, lsu in [(cattle, 4), (sheep, 1)]:
            db.session.add(
                GrazingAllocationLsuBreakdownHistory(
                    farm_id=farm.id,
                    mob_id=mob.id,
                    paddock_id=paddock.id,
                    grazing_session_id=session.id,
                    grazing_allocation_id=allocation.id,
                    animal_group_type_id=group.id,
                    effective_from=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                    effective_to=None,
                    allocation_fraction=1,
                    head_count=4,
                    group_lsu=lsu,
                    allocated_lsu=lsu,
                    source="live",
                )
            )
        db.session.commit()

        report = build_lsu_paddock_tracking_report(
            paddocks=[paddock],
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 1),
            species="",
            metric="current_lsu",
            plot_mode="paddock",
            group_by_species=True,
            include_farm_name=False,
        )

        datasets = report["chart_payload"]["panels"][0]["datasets"]
        assert [row["label"] for row in datasets] == ["West Camp | Cattle", "West Camp | Sheep"]
        assert [row["values"] for row in datasets] == [[4.0], [1.0]]


def test_build_lsu_paddock_tracking_report_supports_head_count_metric_and_min_filter(app):
    with app.app_context():
        farm = Farm(name="Head Metric Farm", timezone="UTC", default_stocking_rate_ha_per_lsu=6)
        db.session.add(farm)
        db.session.flush()
        paddock = Paddock(farm_id=farm.id, name="Head Camp", area_ha=10, grazeable_area_ha=10)
        mob = Mob(farm_id=farm.id, name="Head Mob", status="active")
        db.session.add_all([paddock, mob])
        db.session.flush()
        cattle = AnimalGroupType(species="Cattle", breed="Angus", sex="cow", age_class="adult")
        db.session.add(cattle)
        db.session.flush()
        session = GrazingSession(
            farm_id=farm.id,
            mob_id=mob.id,
            start_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        )
        db.session.add(session)
        db.session.flush()
        allocation = GrazingAllocation(
            grazing_session_id=session.id,
            paddock_id=paddock.id,
            allocation_fraction=0.5,
        )
        db.session.add(allocation)
        db.session.flush()
        db.session.add(
            GrazingAllocationLsuBreakdownHistory(
                farm_id=farm.id,
                mob_id=mob.id,
                paddock_id=paddock.id,
                grazing_session_id=session.id,
                grazing_allocation_id=allocation.id,
                animal_group_type_id=cattle.id,
                effective_from=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
                effective_to=None,
                allocation_fraction=0.5,
                head_count=20,
                group_lsu=20,
                allocated_lsu=10,
                source="live",
            )
        )
        db.session.commit()

        report = build_lsu_paddock_tracking_report(
            paddocks=[paddock],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            species="",
            metric="head_count",
            plot_mode="overlay",
            group_by_species=False,
            include_farm_name=False,
            min_value=10,
        )

        assert report["chart_payload"]["panels"][0]["y_axis_label"] == "Head Count"
        assert report["chart_payload"]["panels"][0]["datasets"][0]["values"] == [10.0]

        filtered = build_lsu_paddock_tracking_report(
            paddocks=[paddock],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            species="",
            metric="head_count",
            plot_mode="overlay",
            group_by_species=False,
            include_farm_name=False,
            min_value=10.01,
        )

        assert filtered["chart_payload"]["panels"] == []
        assert filtered["summary_rows"] == []
        assert filtered["has_history"] is True
