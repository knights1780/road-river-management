
"""Tests for read-only budget reporting services."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget import (
    create_budget_request,
    create_followup_budget_request,
    record_budget_appropriation,
    record_budget_assessment,
    submit_budget_request,
)
from road_river_management.services.budget_reporting import (
    get_budget_request_chain,
    get_fiscal_budget_report,
    get_project_budget_progress_report,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_reporting_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_project(db, name="架空道路修繕事業"):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name
        )
        VALUES (?, ?)
        """,
        (str(uuid4()), name),
    ).lastrowid
    db.commit()
    return project_id


def make_request(
    db,
    project_id,
    amount=30_000_000,
    year=2027,
):
    return create_budget_request(
        db,
        project_id=project_id,
        fiscal_year=year,
        request_title="舗装修繕予算要求",
        requested_amount_yen=amount,
        request_category_code="CONSTRUCTION",
    )


def submitted_request(
    db,
    project_id,
    amount=30_000_000,
    year=2027,
):
    request_id = make_request(
        db, project_id, amount, year
    )
    submit_budget_request(
        db,
        request_id,
        submitted_on="2026-11-01",
    )
    return request_id


def assess(
    db,
    request_id,
    result,
    amount,
):
    return record_budget_assessment(
        db,
        request_id,
        assessment_result_code=result,
        assessed_amount_yen=amount,
        assessed_on="2027-02-15",
    )


def test_empty_fiscal_year_report(db):
    report = get_fiscal_budget_report(db, 2027)

    assert report["request_count"] == 0
    assert report["total_requested_amount_yen"] == 0
    assert report["total_known_assessed_amount_yen"] == 0
    assert report["projects"] == []


def test_unassessed_request_counted_separately(db):
    project_id = make_project(db)
    make_request(db, project_id)

    report = get_fiscal_budget_report(db, 2027)

    assert report["request_count"] == 1
    assert report["assessment_counts"]["UNASSESSED"] == 1
    assert report["total_requested_amount_yen"] == 30_000_000
    assert report["finalized_assessment_count"] == 0


def test_reduced_assessment_summary(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)
    assess(db, request_id, "REDUCED", 22_000_000)

    report = get_fiscal_budget_report(db, 2027)

    assert report["total_requested_amount_yen"] == 30_000_000
    assert (
        report["total_known_assessed_amount_yen"]
        == 22_000_000
    )
    assert (
        report["finalized_assessment_reduction_yen"]
        == 8_000_000
    )
    assert report["assessment_counts"]["REDUCED"] == 1


def test_latest_assessment_only(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    assess(db, request_id, "HELD", None)
    assess(db, request_id, "REDUCED", 20_000_000)

    report = get_fiscal_budget_report(db, 2027)

    assert report["assessment_counts"]["HELD"] == 0
    assert report["assessment_counts"]["REDUCED"] == 1
    assert (
        report["total_known_assessed_amount_yen"]
        == 20_000_000
    )
    assert report["requests"][0]["latest_assessment_round"] == 2


def test_held_is_not_zero_assessment(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    assess(db, request_id, "HELD", None)

    report = get_fiscal_budget_report(db, 2027)

    assert report["assessment_counts"]["HELD"] == 1
    assert report["assessment_counts"]["ZERO"] == 0
    assert report["finalized_assessment_count"] == 0
    assert report["finalized_assessment_reduction_yen"] == 0


def test_zero_assessment_reduction(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    assess(db, request_id, "ZERO", 0)

    report = get_fiscal_budget_report(db, 2027)

    assert report["assessment_counts"]["ZERO"] == 1
    assert report["finalized_assessment_count"] == 1
    assert (
        report["finalized_assessment_reduction_yen"]
        == 30_000_000
    )


def test_rejected_is_not_counted_as_reduction(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    assess(db, request_id, "REJECTED", None)

    report = get_fiscal_budget_report(db, 2027)

    assert report["assessment_counts"]["REJECTED"] == 1
    assert report["finalized_assessment_count"] == 0
    assert report["finalized_assessment_reduction_yen"] == 0


def test_appropriations_do_not_multiply_assessments(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    assess(db, request_id, "HELD", None)
    assess(db, request_id, "REDUCED", 22_000_000)

    record_budget_appropriation(
        db,
        request_id,
        appropriation_type_code="ORIGINAL",
        appropriated_amount_yen=20_000_000,
        approved_on="2027-03-20",
    )
    record_budget_appropriation(
        db,
        request_id,
        appropriation_type_code="SUPPLEMENTAL",
        appropriated_amount_yen=2_000_000,
        approved_on="2027-09-20",
    )

    report = get_fiscal_budget_report(db, 2027)

    assert report["request_count"] == 1
    assert (
        report["total_known_assessed_amount_yen"]
        == 22_000_000
    )
    assert (
        report["total_appropriated_amount_yen"]
        == 22_000_000
    )


def test_multiple_projects_are_grouped(db):
    first = make_project(db, "架空道路A")
    second = make_project(db, "架空道路B")

    make_request(db, first, 10_000_000)
    make_request(db, second, 20_000_000)

    report = get_fiscal_budget_report(db, 2027)

    assert report["request_count"] == 2
    assert report["total_requested_amount_yen"] == 30_000_000
    assert len(report["projects"]) == 2

    filtered = get_fiscal_budget_report(
        db, 2027, project_id=first
    )

    assert filtered["request_count"] == 1
    assert filtered["total_requested_amount_yen"] == 10_000_000


def test_followup_history_and_year_filter(db):
    project_id = make_project(db)
    old_id = submitted_request(db, project_id)

    assess(db, old_id, "ZERO", 0)

    new_id = create_followup_budget_request(
        db,
        old_id,
        fiscal_year=2028,
        requested_amount_yen=8_000_000,
    )

    previous_year = get_fiscal_budget_report(db, 2027)
    next_year = get_fiscal_budget_report(db, 2028)

    assert previous_year["total_requested_amount_yen"] == 30_000_000
    assert next_year["total_requested_amount_yen"] == 8_000_000
    assert next_year["followup_request_count"] == 1

    chain = get_budget_request_chain(db, new_id)

    assert [
        row["budget_request_id"] for row in chain
    ] == [old_id, new_id]


def test_project_progress_is_independent_of_budget(db):
    project_id = make_project(db)
    request_id = submitted_request(db, project_id)

    before = get_project_budget_progress_report(db, project_id)

    assess(db, request_id, "FULL", 30_000_000)

    after = get_project_budget_progress_report(db, project_id)

    assert (
        before["official_progress_percent"]
        == after["official_progress_percent"]
    )
    assert (
        before["reference_progress_percent"]
        == after["reference_progress_percent"]
    )
    assert after["budget_summary"]["finalized_assessment_count"] == 1


def test_invalid_project_and_year_are_rejected(db):
    with pytest.raises(ValueError, match="Project not found"):
        get_project_budget_progress_report(db, 999999)

    with pytest.raises(ValueError):
        get_fiscal_budget_report(db, 1800)

    with pytest.raises(TypeError):
        get_fiscal_budget_report(db, True)


def test_reports_are_read_only(db):
    project_id = make_project(db)
    make_request(db, project_id)

    before = db.total_changes

    get_fiscal_budget_report(db, 2027)
    get_project_budget_progress_report(db, project_id)

    assert db.total_changes == before
    assert not db.in_transaction


def test_database_integrity(db):
    project_id = make_project(db)
    make_request(db, project_id)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
