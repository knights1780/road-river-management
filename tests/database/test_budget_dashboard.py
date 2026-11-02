
"""Tests for the integrated budget dashboard."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget_dashboard import (
    get_integrated_fiscal_dashboard,
)
from road_river_management.services.budget_execution import (
    allocate_contract_to_budget,
    allocate_payment_to_budget,
    record_construction_payment,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_dashboard.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def sample(db):
    work_id = db.execute(
        """
        INSERT INTO construction_works (
            construction_uuid,
            construction_name,
            current_contract_amount_yen
        )
        VALUES (?, '架空橋梁改修契約', 60000000)
        """,
        (str(uuid4()),),
    ).lastrowid

    projects = []
    requests = []

    for index in range(2):
        project_id = db.execute(
            """
            INSERT INTO maintenance_projects (
                project_uuid, project_name
            )
            VALUES (?, ?)
            """,
            (str(uuid4()), f"架空事業{index + 1}"),
        ).lastrowid

        stage_id = db.execute(
            """
            INSERT INTO project_stages (
                stage_uuid, project_id,
                stage_type_code, stage_name
            )
            VALUES (?, ?, 'CONSTRUCTION', '架空工事段階')
            """,
            (str(uuid4()), project_id),
        ).lastrowid

        db.execute(
            """
            INSERT INTO project_construction_links (
                project_id, stage_id, construction_work_id
            )
            VALUES (?, ?, ?)
            """,
            (project_id, stage_id, work_id),
        )

        request_id = db.execute(
            """
            INSERT INTO budget_requests (
                request_uuid, project_id,
                fiscal_year, request_title,
                original_requested_amount_yen,
                current_requested_amount_yen
            )
            VALUES (?, ?, 2027, ?, 30000000, 30000000)
            """,
            (str(uuid4()), project_id, f"架空予算{index + 1}"),
        ).lastrowid

        db.execute(
            """
            INSERT INTO budget_appropriations (
                budget_request_id,
                appropriation_type_code,
                appropriated_amount_yen,
                approved_on
            )
            VALUES (?, 'ORIGINAL', 30000000, '2027-03-20')
            """,
            (request_id,),
        )

        projects.append(project_id)
        requests.append(request_id)

    db.commit()
    return work_id, projects, requests


def allocate(db, work, request, amount):
    return allocate_contract_to_budget(
        db,
        construction_work_id=work,
        budget_request_id=request,
        amount_yen=amount,
        allocation_reason="架空契約の事業別配分",
    )


def pay(db, work, amount=20_000_000):
    return record_construction_payment(
        db,
        construction_work_id=work,
        payment_reference="PAY-001",
        payment_fiscal_year=2027,
        paid_on="2027-06-15",
        paid_amount_yen=amount,
    )


def test_empty_dashboard(db):
    report = get_integrated_fiscal_dashboard(db, 2027)

    assert report["budget_year"]["request_count"] == 0
    assert report["budget_year"][
        "effective_appropriation_yen"
    ] == 0
    assert report["linked_contracts"]["linked_work_count"] == 0
    assert report["projects"] == []


def test_shared_contract_counted_once(db, sample):
    work, _, requests = sample

    allocate(db, work, requests[0], 20_000_000)
    allocate(db, work, requests[1], 40_000_000)

    report = get_integrated_fiscal_dashboard(db, 2027)

    assert report["budget_year"][
        "allocated_contract_yen"
    ] == 60_000_000

    assert report["linked_contracts"][
        "linked_work_count"
    ] == 1

    assert report["linked_contracts"][
        "linked_work_contract_total_yen"
    ] == 60_000_000


def test_appropriation_and_contract_are_separate(
    db, sample
):
    work, _, requests = sample

    allocate(db, work, requests[0], 15_000_000)

    report = get_integrated_fiscal_dashboard(db, 2027)
    budget = report["budget_year"]

    assert budget["effective_appropriation_yen"] == 60_000_000
    assert budget["allocated_contract_yen"] == 15_000_000
    assert budget[
        "unallocated_budget_to_contract_yen_reference"
    ] == 45_000_000


def test_payment_split_does_not_duplicate_cash(db, sample):
    work, _, requests = sample

    allocate(db, work, requests[0], 30_000_000)
    allocate(db, work, requests[1], 30_000_000)

    payment_id = pay(db, work)

    allocate_payment_to_budget(
        db,
        payment_id=payment_id,
        budget_request_id=requests[0],
        amount_yen=8_000_000,
    )
    allocate_payment_to_budget(
        db,
        payment_id=payment_id,
        budget_request_id=requests[1],
        amount_yen=12_000_000,
    )

    report = get_integrated_fiscal_dashboard(db, 2027)

    assert report["payment_year"]["payment_count"] == 1
    assert report["payment_year"]["paid_total_yen"] == 20_000_000
    assert report["budget_year"][
        "allocated_payment_yen_all_years"
    ] == 20_000_000


def test_project_filter_uses_allocated_share(db, sample):
    work, projects, requests = sample

    allocate(db, work, requests[0], 20_000_000)
    allocate(db, work, requests[1], 40_000_000)

    payment_id = pay(db, work)

    allocate_payment_to_budget(
        db,
        payment_id=payment_id,
        budget_request_id=requests[0],
        amount_yen=8_000_000,
    )
    allocate_payment_to_budget(
        db,
        payment_id=payment_id,
        budget_request_id=requests[1],
        amount_yen=12_000_000,
    )

    report = get_integrated_fiscal_dashboard(
        db,
        2027,
        project_id=projects[0],
    )

    assert report["budget_year"]["request_count"] == 1
    assert report["budget_year"][
        "allocated_contract_yen"
    ] == 20_000_000
    assert report["payment_year"][
        "project_allocated_payment_yen"
    ] == 8_000_000
    assert report["payment_year"]["scope"] == (
        "PROJECT_ALLOCATIONS_ONLY"
    )


def test_finalized_carryover_is_shown_separately(
    db, sample
):
    _, _, requests = sample

    db.execute(
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
            carryover_reason
        )
        VALUES (?, ?, 'MEIKYO', 2027, 2028,
                'FINALIZED', 10000000, 8000000,
                '架空工期変更')
        """,
        (str(uuid4()), requests[0]),
    )
    db.commit()

    report = get_integrated_fiscal_dashboard(db, 2027)

    assert report["carryovers_from_year"][
        "total_finalized_carryover_yen"
    ] == 8_000_000

    assert report["budget_year"][
        "effective_appropriation_yen"
    ] == 60_000_000


def test_reports_do_not_write_to_database(db, sample):
    before = db.total_changes

    get_integrated_fiscal_dashboard(db, 2027)

    assert db.total_changes == before
    assert not db.in_transaction


def test_invalid_inputs(db):
    with pytest.raises(TypeError):
        get_integrated_fiscal_dashboard(db, True)

    with pytest.raises(ValueError):
        get_integrated_fiscal_dashboard(db, 1800)

    with pytest.raises(ValueError, match="Project not found"):
        get_integrated_fiscal_dashboard(
            db, 2027, project_id=999999
        )


def test_database_integrity(db, sample):
    report = get_integrated_fiscal_dashboard(db, 2027)

    assert report["budget_year"]["request_count"] == 2
    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"
    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
