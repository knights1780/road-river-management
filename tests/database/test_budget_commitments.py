
"""Tests for multiyear commitments and audit history."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget_commitments import (
    authorize_multiyear_budget,
    create_continuing_expenditure,
    create_obligation,
    get_multiyear_audit_history,
    get_multiyear_budget,
    revise_multiyear_budget,
    set_annual_schedule,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "commitments_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def project_id(db):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name
        )
        VALUES (?, '架空橋梁整備事業')
        """,
        (str(uuid4()),),
    ).lastrowid
    db.commit()
    return project_id


def obligation(db, project_id):
    return create_obligation(
        db,
        project_id=project_id,
        title="架空橋梁整備に係る債務負担行為",
        authorization_fiscal_year=2027,
        start_fiscal_year=2028,
        end_fiscal_year=2030,
        limit_amount_yen=90_000_000,
    )


def continuing(db, project_id):
    return create_continuing_expenditure(
        db,
        project_id=project_id,
        title="架空道路整備継続費",
        start_fiscal_year=2027,
        end_fiscal_year=2029,
        total_amount_yen=120_000_000,
    )


def set_schedule(db, kind, record_id, amounts):
    set_annual_schedule(
        db,
        kind,
        record_id,
        amounts,
        change_reason="年度別配分の登録",
    )


def authorize(db, kind, record_id):
    authorize_multiyear_budget(
        db,
        kind,
        record_id,
        authorized_on="2027-03-20",
        decision_reference="架空議決第001号",
    )


def test_audit_table_exists(db):
    row = db.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'budget_multiyear_audit'
        """
    ).fetchone()
    assert row == ("budget_multiyear_audit",)


def test_create_obligation_and_history(db, project_id):
    record_id = obligation(db, project_id)

    result = get_multiyear_budget(
        db, "OBLIGATION", record_id
    )
    history = get_multiyear_audit_history(
        db, "OBLIGATION", record_id
    )

    assert result["status_code"] == "DRAFT"
    assert result["limit_or_total_yen"] == 90_000_000
    assert [item["action_code"] for item in history] == [
        "CREATED"
    ]


def test_obligation_schedule_can_be_below_limit(
    db, project_id
):
    record_id = obligation(db, project_id)
    set_schedule(
        db,
        "OBLIGATION",
        record_id,
        {2028: 30_000_000, 2029: 40_000_000},
    )
    authorize(db, "OBLIGATION", record_id)

    result = get_multiyear_budget(
        db, "OBLIGATION", record_id
    )

    assert result["status_code"] == "AUTHORIZED"
    assert result["scheduled_total_yen"] == 70_000_000
    assert result["unallocated_difference_yen"] == 20_000_000


def test_obligation_over_limit_rolls_back(db, project_id):
    record_id = obligation(db, project_id)

    with pytest.raises(ValueError, match="exceed"):
        set_schedule(
            db,
            "OBLIGATION",
            record_id,
            {2028: 50_000_000, 2029: 50_000_000},
        )

    result = get_multiyear_budget(
        db, "OBLIGATION", record_id
    )
    assert result["annual_schedule"] == []
    assert len(
        get_multiyear_audit_history(
            db, "OBLIGATION", record_id
        )
    ) == 1
    assert not db.in_transaction


def test_continuing_requires_full_annual_total(
    db, project_id
):
    record_id = continuing(db, project_id)

    set_schedule(
        db,
        "CONTINUING",
        record_id,
        {2027: 20_000_000, 2028: 50_000_000},
    )

    with pytest.raises(ValueError, match="equal"):
        authorize(db, "CONTINUING", record_id)

    result = get_multiyear_budget(
        db, "CONTINUING", record_id
    )
    assert result["status_code"] == "DRAFT"
    assert not db.in_transaction


def test_continuing_authorization(db, project_id):
    record_id = continuing(db, project_id)
    set_schedule(
        db,
        "CONTINUING",
        record_id,
        {
            2027: 20_000_000,
            2028: 50_000_000,
            2029: 50_000_000,
        },
    )
    authorize(db, "CONTINUING", record_id)

    result = get_multiyear_budget(
        db, "CONTINUING", record_id
    )
    assert result["status_code"] == "AUTHORIZED"
    assert result["scheduled_total_yen"] == 120_000_000


def test_authorized_schedule_cannot_be_changed_directly(
    db, project_id
):
    record_id = obligation(db, project_id)
    set_schedule(
        db, "OBLIGATION", record_id,
        {2028: 30_000_000},
    )
    authorize(db, "OBLIGATION", record_id)

    with pytest.raises(ValueError, match="DRAFT"):
        set_schedule(
            db, "OBLIGATION", record_id,
            {2028: 40_000_000},
        )


def test_revision_requires_confirmation(db, project_id):
    record_id = continuing(db, project_id)
    set_schedule(
        db, "CONTINUING", record_id,
        {
            2027: 20_000_000,
            2028: 50_000_000,
            2029: 50_000_000,
        },
    )
    authorize(db, "CONTINUING", record_id)

    with pytest.raises(ValueError, match="confirmation"):
        revise_multiyear_budget(
            db,
            "CONTINUING",
            record_id,
            new_amount_yen=130_000_000,
            new_schedule={
                2027: 20_000_000,
                2028: 55_000_000,
                2029: 55_000_000,
            },
            revised_on="2027-09-20",
            decision_reference="架空補正議決第002号",
            revision_reason="事業費改定",
        )


def test_revision_preserves_before_after_history(
    db, project_id
):
    record_id = continuing(db, project_id)
    set_schedule(
        db, "CONTINUING", record_id,
        {
            2027: 20_000_000,
            2028: 50_000_000,
            2029: 50_000_000,
        },
    )
    authorize(db, "CONTINUING", record_id)

    revise_multiyear_budget(
        db,
        "CONTINUING",
        record_id,
        new_amount_yen=130_000_000,
        new_schedule={
            2027: 20_000_000,
            2028: 55_000_000,
            2029: 55_000_000,
        },
        revised_on="2027-09-20",
        decision_reference="架空補正議決第002号",
        revision_reason="事業費改定",
        confirmed=True,
    )

    result = get_multiyear_budget(
        db, "CONTINUING", record_id
    )
    history = get_multiyear_audit_history(
        db, "CONTINUING", record_id
    )

    assert result["status_code"] == "REVISED"
    assert result["scheduled_total_yen"] == 130_000_000
    assert [item["action_code"] for item in history] == [
        "CREATED",
        "SCHEDULE_CHANGED",
        "AUTHORIZED",
        "REVISED",
    ]
    assert history[-1]["previous"]["record"][
        "total_amount_yen"
    ] == 120_000_000
    assert history[-1]["current"]["record"][
        "total_amount_yen"
    ] == 130_000_000


def test_invalid_revision_rolls_back(db, project_id):
    record_id = continuing(db, project_id)
    set_schedule(
        db, "CONTINUING", record_id,
        {
            2027: 20_000_000,
            2028: 50_000_000,
            2029: 50_000_000,
        },
    )
    authorize(db, "CONTINUING", record_id)

    before_count = len(
        get_multiyear_audit_history(
            db, "CONTINUING", record_id
        )
    )

    with pytest.raises(ValueError, match="equal"):
        revise_multiyear_budget(
            db,
            "CONTINUING",
            record_id,
            new_amount_yen=130_000_000,
            new_schedule={
                2027: 20_000_000,
                2028: 50_000_000,
                2029: 50_000_000,
            },
            revised_on="2027-09-20",
            decision_reference="架空補正議決第002号",
            revision_reason="金額不一致",
            confirmed=True,
        )

    assert len(
        get_multiyear_audit_history(
            db, "CONTINUING", record_id
        )
    ) == before_count
    assert get_multiyear_budget(
        db, "CONTINUING", record_id
    )["limit_or_total_yen"] == 120_000_000
    assert not db.in_transaction


def test_history_cannot_be_updated_or_deleted(
    db, project_id
):
    record_id = obligation(db, project_id)
    audit_id = get_multiyear_audit_history(
        db, "OBLIGATION", record_id
    )[0]["audit_id"]

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            UPDATE budget_multiyear_audit
            SET change_reason = '書換え'
            WHERE audit_id = ?
            """,
            (audit_id,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            DELETE FROM budget_multiyear_audit
            WHERE audit_id = ?
            """,
            (audit_id,),
        )

    db.rollback()


def test_database_integrity(db, project_id):
    record_id = obligation(db, project_id)
    set_schedule(
        db, "OBLIGATION", record_id,
        {2028: 30_000_000},
    )

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
