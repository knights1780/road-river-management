
"""Tests for fiscal-year budget request foundations."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_requests_test.sqlite"
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


def make_stage(db, project_id):
    stage_id = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid,
            project_id,
            stage_type_code,
            stage_name
        )
        VALUES (?, ?, 'CONSTRUCTION', '舗装修繕')
        """,
        (str(uuid4()), project_id),
    ).lastrowid
    db.commit()
    return stage_id


def make_request(
    db,
    project_id,
    *,
    fiscal_year=2027,
    amount=30_000_000,
    stage_id=None,
    previous_request_id=None,
):
    return db.execute(
        """
        INSERT INTO budget_requests (
            request_uuid,
            project_id,
            stage_id,
            fiscal_year,
            request_title,
            request_category_code,
            original_requested_amount_yen,
            current_requested_amount_yen,
            previous_request_id
        )
        VALUES (?, ?, ?, ?, '架空舗装修繕予算要求',
                'CONSTRUCTION', ?, ?, ?)
        """,
        (
            str(uuid4()),
            project_id,
            stage_id,
            fiscal_year,
            amount,
            amount,
            previous_request_id,
        ),
    ).lastrowid


def make_assessment(
    db,
    request_id,
    outcome,
    assessed_amount,
    *,
    round_number=1,
    requested_amount=30_000_000,
):
    return db.execute(
        """
        INSERT INTO budget_assessments (
            budget_request_id,
            assessment_round,
            assessment_result_code,
            requested_amount_at_assessment_yen,
            assessed_amount_yen,
            assessed_on
        )
        VALUES (?, ?, ?, ?, ?, '2026-11-01')
        """,
        (
            request_id,
            round_number,
            outcome,
            requested_amount,
            assessed_amount,
        ),
    ).lastrowid


def test_budget_tables_exist(db):
    expected = {
        "budget_requests",
        "budget_request_revisions",
        "budget_assessments",
        "budget_appropriations",
        "budget_request_events",
    }

    actual = {
        row[0]
        for row in db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        )
    }

    assert expected.issubset(actual)


def test_budget_request_linked_to_project_and_stage(db):
    project_id = make_project(db)
    stage_id = make_stage(db, project_id)

    request_id = make_request(
        db,
        project_id,
        stage_id=stage_id,
    )

    row = db.execute(
        """
        SELECT project_id, stage_id, fiscal_year,
               original_requested_amount_yen
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == (
        project_id,
        stage_id,
        2027,
        30_000_000,
    )


def test_cross_project_stage_is_rejected(db):
    project_a = make_project(db, "架空事業A")
    project_b = make_project(db, "架空事業B")
    stage_b = make_stage(db, project_b)

    with pytest.raises(sqlite3.IntegrityError):
        make_request(
            db,
            project_a,
            stage_id=stage_b,
        )


def test_negative_budget_request_is_rejected(db):
    project_id = make_project(db)

    with pytest.raises(sqlite3.IntegrityError):
        make_request(db, project_id, amount=-1)


def test_reduced_assessment_preserves_requested_amount(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    make_assessment(
        db,
        request_id,
        "REDUCED",
        22_000_000,
    )

    amounts = db.execute(
        """
        SELECT
            r.original_requested_amount_yen,
            r.current_requested_amount_yen,
            a.assessed_amount_yen
        FROM budget_requests AS r
        JOIN budget_assessments AS a
          ON a.budget_request_id = r.budget_request_id
        WHERE r.budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert amounts == (
        30_000_000,
        30_000_000,
        22_000_000,
    )


def test_zero_assessment(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    make_assessment(db, request_id, "ZERO", 0)

    result = db.execute(
        """
        SELECT assessment_result_code,
               assessed_amount_yen
        FROM budget_assessments
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert result == ("ZERO", 0)


def test_held_assessment_has_unknown_amount(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    make_assessment(db, request_id, "HELD", None)

    result = db.execute(
        """
        SELECT assessment_result_code,
               assessed_amount_yen
        FROM budget_assessments
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert result == ("HELD", None)


def test_invalid_assessment_values_are_rejected(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    invalid_cases = [
        ("FULL", 20_000_000),
        ("REDUCED", 30_000_000),
        ("ZERO", 10_000_000),
        ("HELD", 0),
        ("FULL", None),
    ]

    for index, (outcome, amount) in enumerate(
        invalid_cases, start=1
    ):
        with pytest.raises(sqlite3.IntegrityError):
            make_assessment(
                db,
                request_id,
                outcome,
                amount,
                round_number=index,
            )


def test_assessment_rounds_preserve_history(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    make_assessment(db, request_id, "HELD", None)

    make_assessment(
        db,
        request_id,
        "REDUCED",
        22_000_000,
        round_number=2,
    )

    rows = db.execute(
        """
        SELECT assessment_round, assessment_result_code
        FROM budget_assessments
        WHERE budget_request_id = ?
        ORDER BY assessment_round
        """,
        (request_id,),
    ).fetchall()

    assert rows == [
        (1, "HELD"),
        (2, "REDUCED"),
    ]


def test_request_revision_history(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    db.execute(
        """
        INSERT INTO budget_request_revisions (
            budget_request_id,
            revision_number,
            previous_requested_amount_yen,
            new_requested_amount_yen,
            revision_reason
        )
        VALUES (?, 1, 30000000, 28000000, '数量見直し')
        """,
        (request_id,),
    )

    result = db.execute(
        """
        SELECT previous_requested_amount_yen,
               new_requested_amount_yen
        FROM budget_request_revisions
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert result == (30_000_000, 28_000_000)


def test_appropriation_is_independent_from_assessment(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    make_assessment(
        db,
        request_id,
        "REDUCED",
        22_000_000,
    )

    db.execute(
        """
        INSERT INTO budget_appropriations (
            budget_request_id,
            appropriation_type_code,
            appropriated_amount_yen,
            approved_on
        )
        VALUES (?, 'ORIGINAL', 20000000, '2027-03-20')
        """,
        (request_id,),
    )

    row = db.execute(
        """
        SELECT
            a.assessed_amount_yen,
            p.appropriated_amount_yen
        FROM budget_assessments AS a
        JOIN budget_appropriations AS p
          ON p.budget_request_id = a.budget_request_id
        WHERE a.budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert row == (22_000_000, 20_000_000)


def test_next_year_rerequest_preserves_previous(db):
    project_id = make_project(db)
    first_id = make_request(db, project_id, fiscal_year=2027)
    next_id = make_request(
        db,
        project_id,
        fiscal_year=2028,
        amount=8_000_000,
        previous_request_id=first_id,
    )

    row = db.execute(
        """
        SELECT previous_request_id, fiscal_year
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (next_id,),
    ).fetchone()

    assert row == (first_id, 2028)


def test_cross_project_rerequest_is_rejected(db):
    project_a = make_project(db, "架空事業A")
    project_b = make_project(db, "架空事業B")

    first_id = make_request(db, project_a)

    with pytest.raises(sqlite3.IntegrityError):
        make_request(
            db,
            project_b,
            fiscal_year=2028,
            previous_request_id=first_id,
        )


def test_budget_workflow_event(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    db.execute(
        """
        INSERT INTO budget_request_events (
            budget_request_id,
            event_type_code,
            previous_status_code,
            new_status_code,
            event_description
        )
        VALUES (?, 'SUBMITTED', 'DRAFT', 'SUBMITTED',
                '財政担当課へ要求書提出')
        """,
        (request_id,),
    )

    result = db.execute(
        """
        SELECT event_type_code, new_status_code
        FROM budget_request_events
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    assert result == ("SUBMITTED", "SUBMITTED")


def test_database_integrity(db):
    project_id = make_project(db)
    make_request(db, project_id)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
