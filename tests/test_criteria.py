from datetime import date, datetime

import pytest

from redev_radar.analysis.physical.eligibility import FAIL, INDETERMINATE, NO_CRITERIA, PASS, evaluate
from redev_radar.criteria import MissingAssumption, load_criteria_set
from redev_radar.models import RegulationCriterion


def _add(session, **overrides):
    row = dict(
        jurisdiction="11",
        project_type="REDEV_GENERAL",
        item_code="MIN_AREA",
        qualifier="",
        requirement="REQUIRED",
        operator=">=",
        value=10000,
        effective_from=date(2020, 1, 1),
        updated_at=datetime.now(),
    )
    session.add(RegulationCriterion(**{**row, **overrides}))
    session.flush()


def test_criteria_are_selected_by_base_date(session):
    _add(session, value=5000, effective_from=date(2025, 7, 1))

    before = load_criteria_set(session, date(2025, 6, 30))
    after = load_criteria_set(session, date(2025, 7, 1))

    assert before.criterion("MIN_AREA", "11680", "REDEV_GENERAL").value == 10000
    assert after.criterion("MIN_AREA", "11680", "REDEV_GENERAL").value == 5000


def test_expired_criterion_is_not_applied(session):
    row = session.query(RegulationCriterion).filter_by(project_type="REDEV_GENERAL", item_code="MIN_AREA").one()
    row.effective_to = date(2024, 12, 31)
    session.flush()

    assert load_criteria_set(session, date(2024, 12, 31)).criterion("MIN_AREA", "11", "REDEV_GENERAL")
    assert load_criteria_set(session, date(2025, 1, 1)).criterion("MIN_AREA", "11", "REDEV_GENERAL") is None


def test_more_specific_jurisdiction_wins(session):
    _add(session, jurisdiction="11680", value=7000)
    criteria = load_criteria_set(session, date(2026, 1, 1))

    assert criteria.criterion("MIN_AREA", "11680", "REDEV_GENERAL").value == 7000
    assert criteria.criterion("MIN_AREA", "11110", "REDEV_GENERAL").value == 10000
    # 전국 기준은 다른 시도에도 적용되고, 서울 조례 기준은 적용되지 않는다.
    assert criteria.criterion("MAX_AREA", "26110", "REDEV_SMALL").value == 5000
    assert criteria.criterion("MIN_AREA", "26110", "REDEV_GENERAL") is None


def test_no_criteria_before_first_effective_date(session):
    criteria = load_criteria_set(session, date(2019, 12, 31))

    assert criteria.project_criteria("REDEV_GENERAL", "11") == []
    with pytest.raises(MissingAssumption):
        criteria.assumption_value("STATION_AREA_RADIUS_M")


def test_loaded_set_is_not_affected_by_later_edits(session):
    criteria = load_criteria_set(session, date(2026, 1, 1))
    row = session.query(RegulationCriterion).filter_by(project_type="REDEV_GENERAL", item_code="MIN_AREA").one()
    row.value = 1
    session.flush()

    assert criteria.criterion("MIN_AREA", "11", "REDEV_GENERAL").value == 10000


# ---------------------------------------------------------------- 요건 판정


def _general(session):
    return load_criteria_set(session, date(2026, 1, 1)).project_criteria("REDEV_GENERAL", "11")


PASSING = {
    "BLOCK_AREA_M2": 12000,
    "AGED_COUNT_RATIO": 0.7,
    "AGED_FLOOR_RATIO": 0.5,
    "ROAD_ACCESS_RATIO": 0.3,
    "SMALL_PARCEL_RATIO": 0.1,
    "HOUSEHOLD_DENSITY": 40,
    "SEMI_BASEMENT_RATIO": 0.1,
}


def test_pass_needs_all_required_and_minimum_optional(session):
    status, items, summary = evaluate(_general(session), PASSING)

    assert status == PASS
    assert summary["optional_pass"] == 1 and summary["min_optional"] == 1
    assert summary["has_unverified_criteria"] is True
    area = next(i for i in items if i["item_code"] == "MIN_AREA")
    assert area["result"] == PASS and area["basis"]["law_name"]


def test_required_failure_fails(session):
    status, _, _ = evaluate(_general(session), {**PASSING, "BLOCK_AREA_M2": 9999})
    assert status == FAIL


def test_not_enough_optional_fails(session):
    status, _, _ = evaluate(_general(session), {**PASSING, "ROAD_ACCESS_RATIO": 0.9})
    assert status == FAIL


def test_missing_metric_is_indeterminate_not_failed(session):
    status, items, _ = evaluate(_general(session), {**PASSING, "AGED_COUNT_RATIO": None})

    assert status == INDETERMINATE
    assert next(i for i in items if i["item_code"] == "AGED_COUNT_RATIO")["result"] == "UNKNOWN"


def test_missing_optional_metric_that_could_still_pass_is_indeterminate(session):
    metrics = {**PASSING, "ROAD_ACCESS_RATIO": None}
    assert evaluate(_general(session), metrics)[0] == INDETERMINATE


def test_no_registered_criteria(session):
    status, items, summary = evaluate([], PASSING)
    assert status == NO_CRITERIA and items == [] and "reason" in summary
