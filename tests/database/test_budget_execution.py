"""Integration tests for migration 0014 and budget execution service."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget_execution import (
    allocate_contract_to_budget,
    allocate_payment_to_budget,
    get_fiscal_execution_report,
    get_payment_year_report,
    record_construction_payment,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(tmp_path / "budget_execution.sqlite")
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def sample(db):
    projects = []
    requests = []
    work_id = db.execute(
        """INSERT INTO construction_works (
               construction_uuid, construction_name, current_contract_amount_yen
           ) VALUES (?, '架空橋梁改修契約', 60000000)""",
        (str(uuid4()),),
    ).lastrowid
    for index in range(2):
        project_id = db.execute(
            """INSERT INTO maintenance_projects (project_uuid, project_name)
               VALUES (?, ?)""",
            (str(uuid4()), f"架空事業{index + 1}"),
        ).lastrowid
        stage_id = db.execute(
            """INSERT INTO project_stages (
                   stage_uuid, project_id, stage_type_code, stage_name
               ) VALUES (?, ?, 'CONSTRUCTION', '架空施工')""",
            (str(uuid4()), project_id),
        ).lastrowid
        db.execute(
            """INSERT INTO project_construction_links (
                   project_id, stage_id, construction_work_id
               ) VALUES (?, ?, ?)""",
            (project_id, stage_id, work_id),
        )
        req_id = db.execute(
            """INSERT INTO budget_requests (
                   request_uuid, project_id, fiscal_year, request_title,
                   original_requested_amount_yen, current_requested_amount_yen
               ) VALUES (?, ?, ?, ?, 30000000, 30000000)""",
            (str(uuid4()), project_id, 2027 + index, f"架空予算{index + 1}"),
        ).lastrowid
        projects.append(project_id)
        requests.append(req_id)
    db.commit()
    return work_id, requests[0], requests[1]


def allocate(db, work_id, request_id, amount):
    return allocate_contract_to_budget(
        db, construction_work_id=work_id, budget_request_id=request_id,
        amount_yen=amount, allocation_reason="契約費用の年度別按分",
    )


def pay(db, work_id, amount, reference="P-001", year=2027, paid_on="2027-06-01"):
    return record_construction_payment(
        db, construction_work_id=work_id, payment_reference=reference,
        payment_fiscal_year=year, paid_on=paid_on, paid_amount_yen=amount,
    )


def test_tables_exist(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"budget_contract_allocations", "construction_payments", "budget_payment_allocations"} <= tables


def test_shared_contract_not_counted_twice(db, sample):
    work, first, second = sample
    allocate(db, work, first, 20_000_000)
    allocate(db, work, second, 40_000_000)
    first_report = get_fiscal_execution_report(db, 2027)
    second_report = get_fiscal_execution_report(db, 2028)
    assert first_report["total_allocated_contract_yen"] == 20_000_000
    assert second_report["total_allocated_contract_yen"] == 40_000_000
    assert first_report["total_allocated_contract_yen"] + second_report["total_allocated_contract_yen"] == 60_000_000


def test_contract_allocation_cannot_exceed_contract(db, sample):
    work, first, second = sample
    allocate(db, work, first, 40_000_000)
    with pytest.raises(ValueError, match="exceed"):
        allocate(db, work, second, 30_000_000)
    assert not db.in_transaction


def test_duplicate_allocation_rejected(db, sample):
    work, first, _ = sample
    allocate(db, work, first, 10_000_000)
    with pytest.raises(sqlite3.IntegrityError):
        allocate(db, work, first, 10_000_000)


def test_payment_registered_only_once(db, sample):
    work, _, _ = sample
    pay(db, work, 10_000_000)
    with pytest.raises(sqlite3.IntegrityError):
        pay(db, work, 10_000_000)
    assert get_payment_year_report(db, 2027)["paid_total_yen"] == 10_000_000


def test_payments_cannot_exceed_contract(db, sample):
    work, _, _ = sample
    pay(db, work, 50_000_000)
    with pytest.raises(ValueError, match="exceed"):
        pay(db, work, 20_000_000, reference="P-002")


def test_split_payment_counted_once(db, sample):
    work, first, second = sample
    allocate(db, work, first, 30_000_000)
    allocate(db, work, second, 30_000_000)
    payment = pay(db, work, 20_000_000)
    allocate_payment_to_budget(db, payment_id=payment, budget_request_id=first, amount_yen=8_000_000)
    allocate_payment_to_budget(db, payment_id=payment, budget_request_id=second, amount_yen=12_000_000)
    cash = get_payment_year_report(db, 2027)
    assert cash["payment_count"] == 1
    assert cash["paid_total_yen"] == 20_000_000
    assert cash["allocated_payment_yen"] == 20_000_000
    assert get_fiscal_execution_report(db, 2027)["total_allocated_payment_yen_all_years"] == 8_000_000
    assert get_fiscal_execution_report(db, 2028)["total_allocated_payment_yen_all_years"] == 12_000_000


def test_payment_allocation_exceeding_payment_rejected(db, sample):
    work, first, second = sample
    allocate(db, work, first, 30_000_000)
    allocate(db, work, second, 30_000_000)
    payment = pay(db, work, 10_000_000)
    allocate_payment_to_budget(db, payment_id=payment, budget_request_id=first, amount_yen=8_000_000)
    with pytest.raises(ValueError, match="exceed"):
        allocate_payment_to_budget(db, payment_id=payment, budget_request_id=second, amount_yen=3_000_000)


def test_payment_allocation_exceeding_budget_split_rejected(db, sample):
    work, first, _ = sample
    allocate(db, work, first, 10_000_000)
    first_payment = pay(db, work, 8_000_000)
    second_payment = pay(db, work, 8_000_000, reference="P-002")
    allocate_payment_to_budget(db, payment_id=first_payment, budget_request_id=first, amount_yen=8_000_000)
    with pytest.raises(ValueError, match="exceed"):
        allocate_payment_to_budget(db, payment_id=second_payment, budget_request_id=first, amount_yen=3_000_000)


def test_payment_without_budget_split_is_reported(db, sample):
    work, _, _ = sample
    pay(db, work, 5_000_000)
    result = get_payment_year_report(db, 2027)
    assert result["unallocated_payment_yen"] == 5_000_000


def test_fiscal_year_payment_check(db, sample):
    work, _, _ = sample
    with pytest.raises(ValueError, match="fiscal year"):
        pay(db, work, 1_000_000, paid_on="2028-04-01", year=2027)
    assert pay(db, work, 1_000_000, paid_on="2028-03-31", year=2027) > 0


def test_no_unrelated_budget_link(db, sample):
    work, _, _ = sample
    project = db.execute(
        "INSERT INTO maintenance_projects (project_uuid, project_name) VALUES (?, '別事業')",
        (str(uuid4()),),
    ).lastrowid
    request = db.execute(
        """INSERT INTO budget_requests (
               request_uuid, project_id, fiscal_year, request_title,
               original_requested_amount_yen, current_requested_amount_yen
           ) VALUES (?, ?, 2027, '別予算', 1000, 1000)""",
        (str(uuid4()), project),
    ).lastrowid
    db.commit()
    with pytest.raises(ValueError, match="not linked"):
        allocate(db, work, request, 1000)


def test_direct_sql_payment_overallocation_blocked(db, sample):
    work, first, _ = sample
    allocate(db, work, first, 10_000_000)
    payment = pay(db, work, 5_000_000)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO budget_payment_allocations (
                   payment_id, construction_work_id, budget_request_id,
                   allocated_payment_amount_yen
               ) VALUES (?, ?, ?, 6000000)""",
            (payment, work, first),
        )
    db.rollback()


def test_integrity(db, sample):
    work, first, _ = sample
    allocate(db, work, first, 10_000_000)
    payment = pay(db, work, 5_000_000)
    allocate_payment_to_budget(db, payment_id=payment, budget_request_id=first, amount_yen=5_000_000)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
