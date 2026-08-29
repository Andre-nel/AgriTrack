import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import (
    AnimalCohort,
    AnimalGroupBalance,
    AnimalGroupType,
    BreedingCycle,
    BreedingCycleFarm,
    BreedingEnrollment,
    Farm,
    FemaleStatusObservation,
    Mob,
    ParturitionStockEntry,
    PregnancyAssessment,
    StockLedgerEntry,
)
from app.models.stock_ledger import StockEventType
from app.services.allocation_distribution_service import AllocationDistributionService
from app.services.cohort_service import CohortService
from app.services.farm_deletion_service import FarmDeletionService
from app.services.movement_service import MovementService
from app.services.reproduction_service import ReproductionService


def _breeding_stock():
    farm = Farm(name="Reproduction Service Farm", timezone="SAST", active=True)
    db.session.add(farm)
    db.session.flush()
    maternal_mob = Mob(farm_id=farm.id, name="Maternal Mob", status="active")
    offspring_mob = Mob(farm_id=farm.id, name="Offspring Mob", status="active")
    ewe_group = AnimalGroupType(
        species="Sheep",
        breed="Merino",
        sex="ewe",
        age_class="adult",
    )
    db.session.add_all([maternal_mob, offspring_mob, ewe_group])
    db.session.flush()
    balance = AnimalGroupBalance(
        mob_id=maternal_mob.id,
        animal_group_type_id=ewe_group.id,
        head_count=12,
    )
    db.session.add(balance)
    db.session.commit()
    return farm, maternal_mob, offspring_mob, balance


def test_reproductive_workflow_partitions_cohorts_posts_births_and_builds_metrics(app):
    with app.app_context():
        farm, _maternal_mob, offspring_mob, source_balance = _breeding_stock()
        cycle = ReproductionService.create_cycle_from_form(
            {
                "farm_id": str(farm.id),
                "name": "2026 autumn joining",
                "species": "Sheep",
                "exposure_start_date": "2026-03-01",
                "exposure_end_date": "2026-04-01",
            }
        )
        enrollment = ReproductionService.enroll_from_form(
            cycle,
            {
                "balance_id": str(source_balance.id),
                "exposed_count": "10",
            },
        )

        assert enrollment.exposed_count == 10
        assert enrollment.cohort.reproductive_state == "with_sire"
        assert sum(
            row.head_count
            for row in AnimalGroupBalance.query.filter_by(
                animal_group_type_id=source_balance.animal_group_type_id
            ).all()
        ) == 12

        ReproductionService.record_pregnancy_assessment_from_form(
            enrollment,
            {
                "assessed_on": "2026-05-01",
                "pregnant_count": "8",
                "not_pregnant_count": "2",
                "unassessed_count": "0",
                "expected_single_count": "4",
                "expected_twin_count": "4",
                "expected_multiple_count": "0",
                "expected_unknown_litter_count": "0",
                "expected_offspring_total": "12",
            },
        )
        state_counts = {
            state: sum(
                int(balance.head_count)
                for balance in AnimalGroupBalance.query.join(
                    AnimalCohort, AnimalGroupBalance.cohort_id == AnimalCohort.id
                ).filter(AnimalCohort.reproductive_state == state)
            )
            for state in ("pregnant", "not_pregnant", "not_recorded")
        }
        assert state_counts == {"pregnant": 8, "not_pregnant": 2, "not_recorded": 2}

        outcome = ReproductionService.record_parturition_from_form(
            enrollment,
            {
                "period_start_date": "2026-08-01",
                "period_end_date": "2026-08-10",
                "females_parturated_count": "7",
                "single_parturition_count": "3",
                "twin_parturition_count": "4",
                "multiple_parturition_count": "0",
                "unknown_litter_parturition_count": "0",
                "offspring_born_total": "11",
                "offspring_born_alive": "10",
                "offspring_stillborn": "1",
                "strong_at_birth_count": "8",
                "unassessed_at_birth_count": "1",
                "offspring_mob_id": str(offspring_mob.id),
                "offspring_breed": "Merino",
                "offspring_sex": "mixed",
            },
        )
        birth_entry = StockLedgerEntry.query.filter_by(event_type=StockEventType.birth).one()
        birth_balance = AnimalGroupBalance.query.filter_by(
            mob_id=offspring_mob.id,
            cohort_id=birth_entry.cohort_id,
        ).one()
        assert birth_entry.quantity == 10
        assert birth_balance.head_count == 10
        assert ParturitionStockEntry.query.filter_by(outcome_id=outcome.id).count() == 1

        report = ReproductionService.build_analytics_report([cycle])
        assert report["totals"] == {
            "exposed": 10,
            "pregnant": 8,
            "parturated": 7,
            "expected_offspring": 12,
            "expected_quantified_pregnant": 8,
            "total_births": 11,
            "strong_at_birth": 8,
            "birth_observed_exposed": 10,
            "birth_unobserved_exposed": 0,
            "expected_unquantified_pregnant": 0,
            "unassessed_scan": 0,
            "unassessed_birth": 1,
        }
        assert report["metrics"]["parturition_rate_pct"] == pytest.approx(70.0)
        assert report["metrics"]["expected_offspring_per_parturition"] == pytest.approx(1.5)
        assert report["metrics"]["strong_at_birth_pct"] == pytest.approx(72.7272727)
        assert report["metrics"]["strong_per_exposed_female"] == pytest.approx(0.8)

        scan = ReproductionService.latest_effective(
            enrollment.pregnancy_assessments, "assessed_on"
        )
        ReproductionService.record_pregnancy_assessment_from_form(
            enrollment,
            {
                "supersedes_id": str(scan.id),
                "assessed_on": "2026-05-01",
                "pregnant_count": "7",
                "not_pregnant_count": "3",
                "unassessed_count": "0",
                "expected_single_count": "3",
                "expected_twin_count": "4",
                "expected_multiple_count": "0",
                "expected_unknown_litter_count": "0",
                "expected_offspring_total": "11",
                "notes": "Corrected transcribed count",
            },
        )
        ReproductionService.record_parturition_from_form(
            enrollment,
            {
                "supersedes_id": str(outcome.id),
                "period_start_date": "2026-08-01",
                "period_end_date": "2026-08-10",
                "females_parturated_count": "6",
                "single_parturition_count": "2",
                "twin_parturition_count": "4",
                "multiple_parturition_count": "0",
                "unknown_litter_parturition_count": "0",
                "offspring_born_total": "10",
                "offspring_born_alive": "10",
                "offspring_stillborn": "0",
                "strong_at_birth_count": "9",
                "unassessed_at_birth_count": "1",
                "notes": "Corrected observation totals",
            },
        )
        corrected = ReproductionService.build_analytics_report([cycle])
        assert PregnancyAssessment.query.count() == 2
        assert StockLedgerEntry.query.filter_by(event_type=StockEventType.birth).count() == 1
        assert corrected["metrics"]["parturition_rate_pct"] == pytest.approx(60.0)
        assert corrected["metrics"]["expected_offspring_per_parturition"] == pytest.approx(11 / 7)
        assert corrected["metrics"]["strong_at_birth_pct"] == pytest.approx(90.0)


def test_partial_transfer_creates_lineage_and_preserves_reproductive_state(app):
    with app.app_context():
        farm, source_mob, _offspring_mob, source_balance = _breeding_stock()
        destination = Mob(farm_id=farm.id, name="Scan Mob", status="active")
        db.session.add(destination)
        db.session.commit()
        source_cohort = CohortService.ensure_balance_cohort(source_balance)
        source_cohort.reproductive_state = "pregnant"
        source_cohort.expected_litter_size = "twins"
        db.session.commit()

        MovementService.transfer_stock_between_mobs(
            source_mob=source_mob,
            destination_mob=destination,
            transfers=[
                {
                    "animal_group_type_id": str(source_balance.animal_group_type_id),
                    "cohort_id": str(source_cohort.id),
                    "quantity": 5,
                }
            ],
            destination_farm_id=str(farm.id),
        )
        destination_balance = AnimalGroupBalance.query.filter_by(mob_id=destination.id).one()
        assert destination_balance.cohort_id != source_cohort.id
        assert destination_balance.cohort.reproductive_state == "pregnant"
        assert destination_balance.cohort.expected_litter_size == "twins"
        assert destination_balance.head_count == 5


def test_female_status_observation_keeps_dry_and_not_pregnant_independent(app):
    with app.app_context():
        _farm, maternal_mob, _offspring_mob, source_balance = _breeding_stock()

        observation = ReproductionService.record_female_status_from_form(
            maternal_mob,
            {
                "balance_id": str(source_balance.id),
                "observed_on": "2026-07-10",
                "quantity": "4",
                "reproductive_state": "not_pregnant",
                "expected_litter_size": "not_recorded",
                "lactation_state": "dry",
                "offspring_at_foot": "none",
                "notes": "Observed in handling group",
            },
        )

        result = db.session.get(AnimalCohort, observation.result_cohort_id)
        assert FemaleStatusObservation.query.count() == 1
        assert result.reproductive_state == "not_pregnant"
        assert result.lactation_state == "dry"
        assert result.offspring_at_foot == "none"
        assert AnimalGroupBalance.query.filter_by(cohort_id=result.id).one().head_count == 4
        assert AnimalGroupBalance.query.filter_by(mob_id=maternal_mob.id).count() == 2
        context = AllocationDistributionService.balance_context(maternal_mob)
        assert context[str(source_balance.animal_group_type_id)]["head_count"] == 12


def test_farm_deletion_removes_reproductive_records_without_dangling_references(app):
    with app.app_context():
        farm, _maternal_mob, _offspring_mob, source_balance = _breeding_stock()
        farm_id = str(farm.id)
        cycle = ReproductionService.create_cycle_from_form(
            {
                "farm_id": farm_id,
                "name": "Deletion cycle",
                "species": "Sheep",
                "exposure_start_date": "2026-03-01",
            }
        )
        ReproductionService.enroll_from_form(
            cycle,
            {"balance_id": str(source_balance.id), "exposed_count": "6"},
        )

        FarmDeletionService.delete_farm_records(farm)
        db.session.commit()

        assert db.session.get(Farm, farm_id) is None
        assert BreedingCycle.query.count() == 0
        assert BreedingEnrollment.query.count() == 0
        assert AnimalGroupBalance.query.count() == 0


def test_breeding_cycle_can_span_farms_and_survives_one_member_farm_deletion(app):
    with app.app_context():
        first_farm, _first_mob, _offspring_mob, first_balance = _breeding_stock()
        second_farm = Farm(name="Second Reproduction Farm", timezone="SAST", active=True)
        second_mob = Mob(farm=second_farm, name="Second Maternal Mob", status="active")
        db.session.add_all([second_farm, second_mob])
        db.session.flush()
        second_balance = AnimalGroupBalance(
            mob_id=second_mob.id,
            animal_group_type_id=first_balance.animal_group_type_id,
            head_count=9,
        )
        db.session.add(second_balance)
        db.session.commit()

        cycle = ReproductionService.create_cycle_from_form(
            {
                "farm_ids": [str(first_farm.id), str(second_farm.id)],
                "name": "Two-farm joining",
                "species": "Sheep",
                "exposure_start_date": "2026-03-01",
            }
        )
        first_enrollment = ReproductionService.enroll_from_form(
            cycle,
            {"balance_id": str(first_balance.id), "exposed_count": "6"},
        )
        second_enrollment = ReproductionService.enroll_from_form(
            cycle,
            {"balance_id": str(second_balance.id), "exposed_count": "7"},
        )

        assert cycle.farm_ids == [str(first_farm.id), str(second_farm.id)]
        assert cycle.farm_names == "Reproduction Service Farm, Second Reproduction Farm"
        assert {str(row.id) for row in cycle.enrollments} == {
            str(first_enrollment.id),
            str(second_enrollment.id),
        }
        first_report = ReproductionService.build_analytics_report(
            [cycle], farm_id=str(first_farm.id)
        )
        second_report = ReproductionService.build_analytics_report(
            [cycle], farm_id=str(second_farm.id)
        )
        assert first_report["totals"]["exposed"] == 6
        assert second_report["totals"]["exposed"] == 7
        assert first_report["enrollments"][0]["farm_name"] == first_farm.name
        assert second_report["enrollments"][0]["farm_name"] == second_farm.name

        cycle_id = str(cycle.id)
        second_farm_id = str(second_farm.id)
        FarmDeletionService.delete_farm_records(first_farm)
        db.session.commit()
        db.session.expire_all()

        remaining_cycle = db.session.get(BreedingCycle, cycle_id)
        assert remaining_cycle is not None
        assert remaining_cycle.farm_ids == [second_farm_id]
        assert BreedingCycleFarm.query.filter_by(cycle_id=cycle_id).count() == 1
        assert [str(row.id) for row in remaining_cycle.enrollments] == [
            str(second_enrollment.id)
        ]


def test_new_breeding_cycle_form_posts_multiple_farms(client, app):
    with app.app_context():
        farms = [
            Farm(name="Route Farm One", timezone="SAST", active=True),
            Farm(name="Route Farm Two", timezone="SAST", active=True),
        ]
        db.session.add_all(farms)
        db.session.commit()
        farm_ids = [str(farm.id) for farm in farms]

    page = client.get("/reproduction")
    assert page.status_code == 200
    assert b'name="farm_ids" multiple required' in page.data

    response = client.post(
        "/reproduction",
        data={
            "farm_ids": farm_ids,
            "name": "Route multi-farm cycle",
            "species": "Cattle",
            "exposure_start_date": "2026-09-01",
        },
    )
    assert response.status_code == 302

    with app.app_context():
        cycle = BreedingCycle.query.filter_by(name="Route multi-farm cycle").one()
        assert cycle.farm_ids == farm_ids


def test_new_breeding_cycle_database_error_is_reported_without_500(client, monkeypatch):
    def fail_create(_form):
        raise SQLAlchemyError("stale schema")

    monkeypatch.setattr(ReproductionService, "create_cycle_from_form", fail_create)
    response = client.post("/reproduction", data={}, follow_redirects=True)

    assert response.status_code == 200
    assert b"Run &#39;flask db upgrade&#39; and try again" in response.data


def test_analytics_do_not_treat_missing_outcomes_or_expectations_as_zero(app):
    with app.app_context():
        farm, _maternal_mob, _offspring_mob, source_balance = _breeding_stock()
        cycle = ReproductionService.create_cycle_from_form(
            {
                "farm_id": str(farm.id),
                "name": "Incomplete cycle",
                "species": "Sheep",
                "exposure_start_date": "2026-03-01",
            }
        )
        enrollment = ReproductionService.enroll_from_form(
            cycle,
            {"balance_id": str(source_balance.id), "exposed_count": "6"},
        )
        before_scan = ReproductionService.build_analytics_report([cycle])
        assert before_scan["metrics"]["parturition_rate_pct"] is None
        assert before_scan["metrics"]["strong_per_exposed_female"] is None
        assert before_scan["totals"]["birth_unobserved_exposed"] == 6

        ReproductionService.record_pregnancy_assessment_from_form(
            enrollment,
            {
                "assessed_on": "2026-05-01",
                "pregnant_count": "6",
                "not_pregnant_count": "0",
                "unassessed_count": "0",
                "expected_single_count": "0",
                "expected_twin_count": "0",
                "expected_multiple_count": "0",
                "expected_unknown_litter_count": "6",
                "expected_offspring_total": "",
            },
        )
        after_scan = ReproductionService.build_analytics_report([cycle])
        assert after_scan["metrics"]["expected_offspring_per_parturition"] is None
        assert after_scan["totals"]["expected_unquantified_pregnant"] == 6


def test_parturition_can_be_recorded_when_no_pregnancy_scan_was_done(app):
    with app.app_context():
        farm, _maternal_mob, offspring_mob, source_balance = _breeding_stock()
        cycle = ReproductionService.create_cycle_from_form(
            {
                "farm_id": str(farm.id),
                "name": "Unscanned cycle",
                "species": "Sheep",
                "exposure_start_date": "2026-03-01",
            }
        )
        enrollment = ReproductionService.enroll_from_form(
            cycle,
            {"balance_id": str(source_balance.id), "exposed_count": "3"},
        )

        ReproductionService.record_parturition_from_form(
            enrollment,
            {
                "period_start_date": "2026-08-01",
                "females_parturated_count": "3",
                "single_parturition_count": "3",
                "twin_parturition_count": "0",
                "multiple_parturition_count": "0",
                "unknown_litter_parturition_count": "0",
                "offspring_born_total": "3",
                "offspring_born_alive": "3",
                "offspring_stillborn": "0",
                "strong_at_birth_count": "2",
                "unassessed_at_birth_count": "1",
                "offspring_mob_id": str(offspring_mob.id),
                "offspring_breed": "Merino",
                "offspring_sex": "mixed",
            },
        )

        report = ReproductionService.build_analytics_report([cycle])
        assert report["totals"]["pregnant"] == 0
        assert report["totals"]["parturated"] == 3
        assert report["metrics"]["parturition_rate_pct"] == pytest.approx(100.0)
