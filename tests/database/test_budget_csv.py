
"""Tests for budget and construction CSV exports."""

import csv
import io
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget_csv import (
    export_contract_allocation_csv,
    export_fiscal_budget_csv,
    export_payment_year_csv,
    get_payment_export_summary,
    write_csv,
)
from road_river_management.services.budget_execution import (
    allocate_contract_to_budget,
    allocate_payment_to_budget,
    record_construction_payment,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_csv.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def sample(db):
    work = db.execute(
        """
        INSERT INTO construction_works (
            construction_uuid,
            construction_name,
            current_contract_amount_yen
        )
        VALUES (?, '架空道路改良工事', 60000000)
        """,
        (str(uuid4()),),
    ).lastrowid

    requests = []

    for index in range(2):
        project = db.execute(
            """
            INSERT INTO maintenance_projects (
                project_uuid, project_name
            )
            VALUES (?, ?)
            """,
            (str(uuid4()), f"架空事業{index + 1}"),
        ).lastrowid

        stage = db.execute(
            """
            INSERT INTO project_stages (
                stage_uuid, project_id,
                stage_type_code, stage_name
            )
            VALUES (?, ?, 'CONSTRUCTION', '工事段階')
            """,
            (str(uuid4()), project),
        ).lastrowid

        db.execute(
            """
            INSERT INTO project_construction_links (
                project_id, stage_id, construction_work_id
            )
            VALUES (?, ?, ?)
            """,
            (project, stage, work),
        )

        request = db.execute(
            """
            INSERT INTO budget_requests (
                request_uuid,
                project_id,
                fiscal_year,
                request_title,
                original_requested_amount_yen,
                current_requested_amount_yen
            )
            VALUES (?, ?, 2027, ?, 30000000, 30000000)
            """,
            (str(uuid4()), project, f"予算要求{index + 1}"),
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
            (request,),
        )

        requests.append(request)

    db.commit()
    return work, requests


def parsed_csv(data):
    return list(
        csv.DictReader(
            io.StringIO(
                data.decode("utf-8-sig"),
                newline="",
            )
        )
    )


def allocate(db, work, request, amount):
    allocate_contract_to_budget(
        db,
        construction_work_id=work,
        budget_request_id=request,
        amount_yen=amount,
        allocation_reason="架空事業別配分",
    )


def payment(db, work):
    return record_construction_payment(
        db,
        construction_work_id=work,
        payment_reference="PAY-001",
        payment_fiscal_year=2027,
        paid_on="2027-06-15",
        paid_amount_yen=20_000_000,
    )


def test_budget_csv_has_bom_and_japanese_headers(db, sample):
    content = export_fiscal_budget_csv(db, 2027)

    assert content.startswith(b"\xef\xbb\xbf")

    rows = parsed_csv(content)
    assert len(rows) == 2
    assert "事業名" in rows[0]
    assert "補正後成立予算額_円" in rows[0]


def test_budget_csv_amounts(db, sample):
    work, requests = sample
    allocate(db, work, requests[0], 20_000_000)
    allocate(db, work, requests[1], 40_000_000)

    rows = parsed_csv(
        export_fiscal_budget_csv(db, 2027)
    )

    assert sum(
        int(row["契約配分額_円"]) for row in rows
    ) == 60_000_000

    assert sum(
        int(row["補正後成立予算額_円"]) for row in rows
    ) == 60_000_000


def test_contract_csv_does_not_duplicate_work(db, sample):
    work, requests = sample
    allocate(db, work, requests[0], 20_000_000)
    allocate(db, work, requests[1], 40_000_000)

    rows = parsed_csv(
        export_contract_allocation_csv(db, 2027)
    )

    assert len(rows) == 2
    assert len({
        row["工事契約ID"] for row in rows
    }) == 1
    assert sum(
        int(row["契約配分額_円"]) for row in rows
    ) == 60_000_000


def test_payment_csv_counts_split_payment_once(db, sample):
    work, requests = sample
    allocate(db, work, requests[0], 30_000_000)
    allocate(db, work, requests[1], 30_000_000)

    pid = payment(db, work)

    allocate_payment_to_budget(
        db,
        payment_id=pid,
        budget_request_id=requests[0],
        amount_yen=8_000_000,
    )

    allocate_payment_to_budget(
        db,
        payment_id=pid,
        budget_request_id=requests[1],
        amount_yen=12_000_000,
    )

    rows = parsed_csv(
        export_payment_year_csv(db, 2027)
    )

    assert len(rows) == 1
    assert rows[0]["支払額_円"] == "20000000"
    assert rows[0][
        "予算配分済支払額_円"
    ] == "20000000"
    assert rows[0]["未配分支払額_円"] == "0"

    control = get_payment_export_summary(db, 2027)
    assert control["paid_total_yen"] == 20_000_000


def test_unallocated_payment_is_visible(db, sample):
    work, _ = sample
    payment(db, work)

    rows = parsed_csv(
        export_payment_year_csv(db, 2027)
    )

    assert rows[0]["未配分支払額_円"] == "20000000"


def test_formula_like_name_is_escaped(db, sample):
    work, _ = sample

    db.execute(
        """
        UPDATE construction_works
        SET construction_name = '=1+1'
        WHERE construction_work_id = ?
        """,
        (work,),
    )
    db.commit()

    payment(db, work)

    rows = parsed_csv(
        export_payment_year_csv(db, 2027)
    )

    assert rows[0]["工事名称"] == "'=1+1"


def test_export_is_read_only(db, sample):
    before = db.total_changes

    export_fiscal_budget_csv(db, 2027)
    export_contract_allocation_csv(db, 2027)
    export_payment_year_csv(db, 2027)

    assert db.total_changes == before
    assert not db.in_transaction


def test_write_csv_protects_existing_file(
    db, sample, tmp_path
):
    path = tmp_path / "budget.csv"
    content = export_fiscal_budget_csv(db, 2027)

    write_csv(path, content)

    assert path.read_bytes() == content

    with pytest.raises(FileExistsError):
        write_csv(path, content)


def test_invalid_fiscal_year_rejected(db):
    with pytest.raises(TypeError):
        export_fiscal_budget_csv(db, True)

    with pytest.raises(ValueError):
        export_contract_allocation_csv(db, 1800)

    with pytest.raises(TypeError):
        export_payment_year_csv(db, "2027")
