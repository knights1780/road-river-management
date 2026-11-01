
"""Tests for budget request and assessment services."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget import (
    create_budget_request,
    create_followup_budget_request,
    get_project_budget_summary,
    record_budget_appropriation,
    record_budget_assessment,
    revise_budget_request,
    submit_budget_request,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_service_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def project_id(db):
    result = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name
        )
        VALUES (?, ?)
        """,
        (str(uuid4()), "架空道路修繕事業"),
    )
    db.commit()
    return result.lastrowid


def make_request(db, project_id, amount=30_000_000):
    return create_budget_request(
        db,
        project_id=project_id,
        fiscal_year=2027,
        request_title="道路舗装修繕",
        requested_amount_yen=amount,
        request_category_code="CONSTRUCTION",
    )


def submitted_request(db, project_id, amount=30_000_000):
    request_id = make_request(db, project_id, amount)
    submit_budget_request(
        db,
        request_id,
        submitted_on="2026-11-01",
    )
    return request_id


def assess(
    db,
    request_id,
    result="REDUCED",
    amount=22_000_000,
):
    return record_budget_assessment(
        db,
        request_id,
        assessment_result_code=result,
        assessed_amount_yen=amount,
        assessed_on="2027-02-15",
        assessment_reason="財政査定",
    )


def test_create_request_and_event(db, project_id):
    request_id = make_request(db, project_id)

    row = db.execute(
        """
        SELECT request_status_code,
               original_requested_amount_yen,
               current_requested_amount_yen
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == ("DRAFT", 30_000_000, 30_000_000)

    events = db.execute(
        """
        SELECT event_type_code
        FROM budget_request_events
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchall()

    assert events == [("CREATED",)]


def test_invalid_request_rolls_back(db, project_id):
    before = db.execute(
        "SELECT COUNT(*) FROM budget_requests"
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        create_budget_request(
            db,
            project_id=project_id,
            stage_id=999999,
            fiscal_year=2027,
            request_title="不正な工程参照",
            requested_amount_yen=100_000,
        )

    assert db.execute(
        "SELECT COUNT(*) FROM budget_requests"
    ).fetchone()[0] == before

    assert not db.in_transaction


def test_revision_changes_current_amount_only(db, project_id):
    request_id = make_request(db, project_id)

    revision = revise_budget_request(
        db,
        request_id,
        new_requested_amount_yen=28_000_000,
        revision_reason="数量の精査",
    )

    assert revision == 1

    row = db.execute(
        """
        SELECT original_requested_amount_yen,
               current_requested_amount_yen
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == (30_000_000, 28_000_000)


def test_multiple_revisions_preserve_history(db, project_id):
    request_id = make_request(db, project_id)

    revise_budget_request(
        db,
        request_id,
        new_requested_amount_yen=28_000_000,
        revision_reason="数量変更",
    )

    revise_budget_request(
        db,
        request_id,
        new_requested_amount_yen=25_000_000,
        revision_reason="単価見直し",
    )

    rows = db.execute(
        """
        SELECT revision_number,
               previous_requested_amount_yen,
               new_requested_amount_yen
        FROM budget_request_revisions
        WHERE budget_request_id = ?
        ORDER BY revision_number
        """,
        (request_id,),
    ).fetchall()

    assert rows == [
        (1, 30_000_000, 28_000_000),
        (2, 28_000_000, 25_000_000),
    ]


def test_submit_request(db, project_id):
    request_id = make_request(db, project_id)

    submit_budget_request(
        db,
        request_id,
        submitted_on="2026-11-01",
    )

    row = db.execute(
        """
        SELECT request_status_code, submitted_on
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == ("SUBMITTED", "2026-11-01")


def test_duplicate_submission_rejected(db, project_id):
    request_id = submitted_request(db, project_id)

    before = db.execute(
        """
        SELECT COUNT(*)
        FROM budget_request_events
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    with pytest.raises(ValueError, match="Only draft"):
        submit_budget_request(
            db,
            request_id,
            submitted_on="2026-11-02",
        )

    after = db.execute(
        """
        SELECT COUNT(*)
        FROM budget_request_events
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    assert before == after


def test_reduced_assessment(db, project_id):
    request_id = submitted_request(db, project_id)

    round_number = assess(db, request_id)

    assert round_number == 1

    row = db.execute(
        """
        SELECT r.request_status_code,
               r.current_requested_amount_yen,
               a.assessed_amount_yen
        FROM budget_requests AS r
        JOIN budget_assessments AS a
          ON a.budget_request_id = r.budget_request_id
        WHERE r.budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == ("ASSESSED", 30_000_000, 22_000_000)


def test_held_then_reassessed(db, project_id):
    request_id = submitted_request(db, project_id)

    first_round = assess(
        db,
        request_id,
        result="HELD",
        amount=None,
    )

    second_round = assess(
        db,
        request_id,
        result="REDUCED",
        amount=20_000_000,
    )

    assert (first_round, second_round) == (1, 2)

    status = db.execute(
        """
        SELECT request_status_code
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    assert status == "ASSESSED"


def test_invalid_assessment_preserves_request(db, project_id):
    request_id = submitted_request(db, project_id)

    with pytest.raises(ValueError):
        assess(
            db,
            request_id,
            result="FULL",
            amount=10_000_000,
        )

    count = db.execute(
        """
        SELECT COUNT(*)
        FROM budget_assessments
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    status = db.execute(
        """
        SELECT request_status_code
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    assert count == 0
    assert status == "SUBMITTED"
    assert not db.in_transaction


def test_appropriation_separate_from_assessment(db, project_id):
    request_id = submitted_request(db, project_id)
    assess(db, request_id)

    appropriation_id = record_budget_appropriation(
        db,
        request_id,
        appropriation_type_code="ORIGINAL",
        appropriated_amount_yen=20_000_000,
        approved_on="2027-03-20",
    )

    assert appropriation_id > 0

    summary = get_project_budget_summary(db, project_id)
    item = summary["requests"][0]

    assert item["current_requested_amount_yen"] == 30_000_000
    assert item["latest_assessed_amount_yen"] == 22_000_000
    assert item["total_appropriated_amount_yen"] == 20_000_000


def test_duplicate_appropriation_rolls_back(db, project_id):
    request_id = submitted_request(db, project_id)
    assess(db, request_id)

    arguments = {
        "appropriation_type_code": "ORIGINAL",
        "appropriated_amount_yen": 20_000_000,
        "approved_on": "2027-03-20",
    }

    record_budget_appropriation(db, request_id, **arguments)

    with pytest.raises(sqlite3.IntegrityError):
        record_budget_appropriation(
            db,
            request_id,
            **arguments,
        )

    count = db.execute(
        """
        SELECT COUNT(*)
        FROM budget_appropriations
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    assert count == 1
    assert not db.in_transaction


def test_appropriation_rejects_held_request(db, project_id):
    request_id = submitted_request(db, project_id)
    assess(db, request_id, result="HELD", amount=None)

    with pytest.raises(ValueError):
        record_budget_appropriation(
            db,
            request_id,
            appropriation_type_code="ORIGINAL",
            appropriated_amount_yen=10_000_000,
            approved_on="2027-03-20",
        )


def test_followup_request_is_new_record(db, project_id):
    old_id = submitted_request(db, project_id)
    assess(db, old_id, result="ZERO", amount=0)

    new_id = create_followup_budget_request(
        db,
        old_id,
        fiscal_year=2028,
        requested_amount_yen=8_000_000,
    )

    assert new_id != old_id

    row = db.execute(
        """
        SELECT fiscal_year,
               previous_request_id,
               current_requested_amount_yen,
               request_status_code
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (new_id,),
    ).fetchone()

    assert row == (2028, old_id, 8_000_000, "DRAFT")


def test_followup_requires_future_year(db, project_id):
    old_id = make_request(db, project_id)

    with pytest.raises(ValueError, match="later"):
        create_followup_budget_request(
            db,
            old_id,
            fiscal_year=2027,
            requested_amount_yen=5_000_000,
        )


def test_budget_summary_filters_fiscal_year(db, project_id):
    original_id = make_request(db, project_id)

    create_followup_budget_request(
        db,
        original_id,
        fiscal_year=2028,
        requested_amount_yen=8_000_000,
    )

    full_summary = get_project_budget_summary(db, project_id)

    assert full_summary["request_count"] == 2
    assert (
        full_summary["total_current_requested_amount_yen"]
        == 38_000_000
    )

    filtered = get_project_budget_summary(
        db,
        project_id,
        fiscal_year=2028,
    )

    assert filtered["request_count"] == 1
    assert (
        filtered["total_current_requested_amount_yen"]
        == 8_000_000
    )


def test_invalid_amount_types(db, project_id):
    with pytest.raises(TypeError):
        create_budget_request(
            db,
            project_id=project_id,
            fiscal_year=2027,
            request_title="不正金額",
            requested_amount_yen=True,
        )

    with pytest.raises(ValueError):
        create_budget_request(
            db,
            project_id=project_id,
            fiscal_year=2027,
            request_title="不正金額",
            requested_amount_yen=-100,
        )


def test_database_integrity(db, project_id):
    request_id = submitted_request(db, project_id)
    assess(db, request_id)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
