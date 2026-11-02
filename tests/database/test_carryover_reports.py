
"""Tests for carryover reporting and CSV exports."""

import csv
import io
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.carryover_reports import (
    export_carryover_list_csv,
    export_carryover_reason_csv,
    export_carryover_review_csv,
    get_carryover_list_report,
    get_carryover_reason_report,
    get_carryover_review_report,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "carryover_reports.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_carryover(
    db,
    *,
    kind="MEIKYO",
    status="FINALIZED",
    amount=8_000_000,
    reason="架空工事の工期延長",
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

    cid = db.execute(
        """
        INSERT INTO budget_carryovers (
            carryover_uuid,
            budget_request_id,
            carryover_type_code,
            source_fiscal_year,
            destination_fiscal_year,
            carryover_status_code,
            authorized_limit_yen,
            actual_carryover_amount_yen,
            carryover_reason,
            unavoidable_accident_description,
            obligation_incurred_on,
            authorization_date,
            finalization_date,
            decision_reference
        )
        VALUES (
            ?, ?, ?, 2027, 2028, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            str(uuid4()),
            request_id,
            kind,
            status,
            10_000_000 if status != "DRAFT" else None,
            amount if status == "FINALIZED" else None,
            reason,
            "避け難い事故" if kind == "ACCIDENT" else None,
            "2027-10-01" if kind == "ACCIDENT" else None,
            "2028-03-20" if status != "DRAFT" else None,
            "2028-04-01" if status == "FINALIZED" else None,
            "架空決定第001号" if status != "DRAFT" else None,
        ),
    ).lastrowid

    db.commit()
    return cid


def read_csv(data):
    return list(
        csv.DictReader(
            io.StringIO(data.decode("utf-8-sig"))
        )
    )


def test_reason_report_contains_project(db):
    cid = make_carryover(db)

    report = get_carryover_reason_report(db, cid)

    assert report["project_name"] == "架空道路整備事業"
    assert report["carryover_type_name"] == "繰越明許費"
    assert report["actual_carryover_amount_yen"] == 8_000_000
    assert report["official_document"] is False


def test_reason_csv_has_japanese_labels(db):
    cid = make_carryover(db)

    data = export_carryover_reason_csv(db, cid)

    assert data.startswith(b"\xef\xbb\xbf")
    rows = read_csv(data)

    fields = {row["項目"]: row["内容"] for row in rows}

    assert fields["事業名"] == "架空道路整備事業"
    assert fields["確定繰越額_円"] == "8000000"
    assert fields["繰越理由"] == "架空工事の工期延長"


def test_list_finalized_total(db):
    make_carryover(db, amount=8_000_000)
    make_carryover(db, status="DRAFT")

    report = get_carryover_list_report(db, 2027)

    assert report["record_count"] == 2
    assert report["finalized_count"] == 1
    assert report["finalized_total_yen"] == 8_000_000


def test_list_filters_carryover_type(db):
    make_carryover(db, kind="MEIKYO")
    make_carryover(db, kind="ACCIDENT")

    report = get_carryover_list_report(
        db, 2027, carryover_type_code="ACCIDENT"
    )

    assert report["record_count"] == 1
    assert report["records"][0]["carryover_type_code"] == (
        "ACCIDENT"
    )


def test_list_csv_has_one_row_per_carryover(db):
    make_carryover(db, amount=8_000_000)
    make_carryover(db, amount=6_000_000)

    rows = read_csv(export_carryover_list_csv(db, 2027))

    assert len(rows) == 2
    assert sum(
        int(row["確定繰越額_円"]) for row in rows
    ) == 14_000_000


def test_review_report_uses_reason_specific_checks(db):
    cid = make_carryover(db, kind="ACCIDENT")

    report = get_carryover_review_report(
        db,
        cid,
        reason_code="DISASTER",
    )

    checklist = report["checklist"]

    assert "DISASTER_RECORD" in (
        checklist["missing_document_codes"]
    )
    assert "OBLIGATION_RECORD" in (
        checklist["missing_document_codes"]
    )
    assert checklist["legal_eligibility_confirmed"] is False


def test_review_csv_shows_missing_documents(db):
    cid = make_carryover(db)

    rows = read_csv(
        export_carryover_review_csv(
            db,
            cid,
            reason_code="LAND",
        )
    )

    assert any(
        row["コード"] == "LAND_NEGOTIATION"
        and row["状況"] == "未申告"
        for row in rows
    )
    assert any(
        row["分類"] == "担当者確認"
        for row in rows
    )


def test_csv_formula_injection_is_escaped(db):
    cid = make_carryover(
        db, reason="=1+1"
    )

    rows = read_csv(
        export_carryover_reason_csv(db, cid)
    )

    fields = {row["項目"]: row["内容"] for row in rows}
    assert fields["繰越理由"] == "'=1+1"


def test_missing_carryover_fails(db):
    with pytest.raises(ValueError, match="not found"):
        get_carryover_reason_report(db, 999999)


def test_invalid_year_fails(db):
    with pytest.raises(TypeError):
        get_carryover_list_report(db, True)

    with pytest.raises(ValueError):
        get_carryover_list_report(db, 1800)


def test_reports_are_read_only(db):
    cid = make_carryover(db)
    before = db.total_changes

    get_carryover_reason_report(db, cid)
    get_carryover_list_report(db, 2027)
    get_carryover_review_report(
        db, cid, reason_code="DESIGN"
    )

    assert db.total_changes == before
    assert not db.in_transaction


def test_database_integrity(db):
    make_carryover(db)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
