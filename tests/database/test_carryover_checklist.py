
"""Tests for reason-specific carryover checklists."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.carryover_checklist import (
    get_carryover_checklist,
    list_carryover_reason_options,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "carryover_checklist.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_carryover(
    db,
    kind="MEIKYO",
    status="DRAFT",
):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name
        )
        VALUES (?, '架空道路整備事業')
        """,
        (str(uuid4()),),
    ).lastrowid

    request_id = db.execute(
        """
        INSERT INTO budget_requests (
            request_uuid,
            project_id,
            fiscal_year,
            request_title,
            original_requested_amount_yen,
            current_requested_amount_yen
        )
        VALUES (?, ?, 2027, '架空工事予算',
                30000000, 30000000)
        """,
        (str(uuid4()), project_id),
    ).lastrowid

    accident = kind == "ACCIDENT"

    cid = db.execute(
        """
        INSERT INTO budget_carryovers (
            carryover_uuid,
            budget_request_id,
            carryover_type_code,
            source_fiscal_year,
            destination_fiscal_year,
            carryover_status_code,
            carryover_reason,
            unavoidable_accident_description,
            obligation_incurred_on,
            authorized_limit_yen,
            authorization_date,
            decision_reference,
            actual_carryover_amount_yen,
            finalization_date
        )
        VALUES (
            ?, ?, ?, 2027, 2028, ?,
            '架空の工事遅延',
            ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        (
            str(uuid4()),
            request_id,
            kind,
            status,
            "架空の不可避な施工障害" if accident else None,
            "2027-10-01" if accident else None,
            10_000_000 if status != "DRAFT" else None,
            "2028-03-20" if status != "DRAFT" else None,
            "架空決定第001号" if status != "DRAFT" else None,
            8_000_000 if status == "FINALIZED" else None,
            "2028-04-01" if status == "FINALIZED" else None,
        ),
    ).lastrowid
    db.commit()
    return cid


def test_reason_options():
    options = list_carryover_reason_options()

    assert len(options) == 6
    assert {item["code"] for item in options} == {
        "LAND",
        "CONSULTATION",
        "DESIGN",
        "PROCUREMENT",
        "DISASTER",
        "OTHER",
    }


def test_land_checklist(db):
    cid = make_carryover(db)

    result = get_carryover_checklist(
        db, cid, reason_code="LAND"
    )

    assert "LAND_NEGOTIATION" in result[
        "missing_document_codes"
    ]
    assert "REVISED_SCHEDULE" in result[
        "missing_document_codes"
    ]
    assert result["data_complete"] is False


def test_consultation_has_specific_document(db):
    cid = make_carryover(db)

    result = get_carryover_checklist(
        db, cid, reason_code="CONSULTATION"
    )

    assert "CONSULTATION_RECORD" in result[
        "missing_document_codes"
    ]
    assert "LAND_NEGOTIATION" not in result[
        "missing_document_codes"
    ]


def test_accident_has_additional_checks(db):
    cid = make_carryover(db, kind="ACCIDENT")

    result = get_carryover_checklist(
        db, cid, reason_code="DISASTER"
    )

    assert "OBLIGATION_RECORD" in result[
        "missing_document_codes"
    ]
    assert "ACCIDENT_RECORD" in result[
        "missing_document_codes"
    ]
    assert any(
        "支出負担行為" in text
        for text in result["manual_review_items"]
    )


def test_meikyo_has_budget_checks(db):
    cid = make_carryover(db)

    result = get_carryover_checklist(
        db, cid, reason_code="DESIGN"
    )

    assert any(
        "繰越明許費の予算上の定め" in text
        for text in result["manual_review_items"]
    )


def test_declared_documents_are_not_missing(db):
    cid = make_carryover(db)

    result = get_carryover_checklist(
        db,
        cid,
        reason_code="LAND",
        evidence_codes={
            "REVISED_SCHEDULE",
            "AMOUNT_BREAKDOWN",
            "LAND_NEGOTIATION",
        },
    )

    assert result["data_complete"] is True
    assert result["missing_document_codes"] == []
    assert result["legal_eligibility_confirmed"] is False
    assert result["approval_confirmed_by_checklist"] is False


def test_unknown_evidence_is_reported(db):
    cid = make_carryover(db)

    result = get_carryover_checklist(
        db,
        cid,
        reason_code="LAND",
        evidence_codes={"UNKNOWN_CODE"},
    )

    assert result["unknown_evidence_codes"] == [
        "UNKNOWN_CODE"
    ]
    assert result["data_complete"] is False


def test_finalized_carryover(db):
    cid = make_carryover(db, status="FINALIZED")

    result = get_carryover_checklist(
        db, cid, reason_code="PROCUREMENT"
    )

    assert result["missing_field_codes"] == []
    assert result["carryover_status_code"] == "FINALIZED"


def test_invalid_reason_is_rejected(db):
    cid = make_carryover(db)

    with pytest.raises(ValueError, match="Unknown"):
        get_carryover_checklist(
            db, cid, reason_code="INVALID"
        )


def test_missing_carryover_is_rejected(db):
    with pytest.raises(ValueError, match="not found"):
        get_carryover_checklist(
            db, 999999, reason_code="OTHER"
        )


def test_read_only(db):
    cid = make_carryover(db)
    before = db.total_changes

    get_carryover_checklist(
        db, cid, reason_code="DISASTER"
    )

    assert db.total_changes == before
    assert not db.in_transaction


def test_integrity(db):
    cid = make_carryover(db, kind="ACCIDENT")

    get_carryover_checklist(
        db, cid, reason_code="DISASTER"
    )

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"
    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
