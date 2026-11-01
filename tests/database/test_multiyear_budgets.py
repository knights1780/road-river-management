
"""Database tests for multiyear budget migration 0012."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "multiyear_budget_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_project(db):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name
        )
        VALUES (?, '架空道路改良事業')
        """,
        (str(uuid4()),),
    ).lastrowid
    db.commit()
    return project_id


def make_request(db, project_id, year=2027):
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
        VALUES (?, ?, ?, '架空工事予算', 30000000, 30000000)
        """,
        (str(uuid4()), project_id, year),
    ).lastrowid
    db.commit()
    return request_id


def make_appropriation(db, request_id):
    appropriation_id = db.execute(
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
    ).lastrowid
    db.commit()
    return appropriation_id


def make_obligation(db, project_id):
    obligation_id = db.execute(
        """
        INSERT INTO budget_obligations (
            obligation_uuid,
            project_id,
            obligation_title,
            authorization_fiscal_year,
            start_fiscal_year,
            end_fiscal_year,
            limit_amount_yen
        )
        VALUES (?, ?, '架空橋梁修繕契約',
                2027, 2028, 2030, 90000000)
        """,
        (str(uuid4()), project_id),
    ).lastrowid
    db.commit()
    return obligation_id


def make_continuing(db, project_id):
    continuing_id = db.execute(
        """
        INSERT INTO budget_continuing_expenditures (
            continuing_uuid,
            project_id,
            continuing_title,
            start_fiscal_year,
            end_fiscal_year,
            total_amount_yen
        )
        VALUES (?, ?, '架空道路整備',
                2027, 2029, 120000000)
        """,
        (str(uuid4()), project_id),
    ).lastrowid
    db.commit()
    return continuing_id


def test_multiyear_tables_exist(db):
    expected = {
        "budget_appropriation_adjustments",
        "budget_carryovers",
        "budget_obligations",
        "budget_obligation_years",
        "budget_continuing_expenditures",
        "budget_continuing_years",
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


def test_appropriation_adjustment(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)
    appropriation_id = make_appropriation(db, request_id)

    db.execute(
        """
        INSERT INTO budget_appropriation_adjustments (
            adjustment_uuid,
            budget_appropriation_id,
            adjustment_number,
            adjustment_type_code,
            amount_delta_yen,
            approved_on,
            adjustment_reason
        )
        VALUES (?, ?, 1, 'DECREASE', -2000000,
                '2027-09-20', '補正予算による減額')
        """,
        (str(uuid4()), appropriation_id),
    )

    adjustment = db.execute(
        """
        SELECT amount_delta_yen
        FROM budget_appropriation_adjustments
        WHERE budget_appropriation_id = ?
        """,
        (appropriation_id,),
    ).fetchone()[0]

    assert adjustment == -2_000_000


def test_invalid_adjustment_direction(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)
    appropriation_id = make_appropriation(db, request_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_appropriation_adjustments (
                adjustment_uuid,
                budget_appropriation_id,
                adjustment_number,
                adjustment_type_code,
                amount_delta_yen,
                approved_on,
                adjustment_reason
            )
            VALUES (?, ?, 1, 'DECREASE', 1000000,
                    '2027-09-20', '不正な減額')
            """,
            (str(uuid4()), appropriation_id),
        )


def test_meikyo_carryover(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    carryover_id = db.execute(
        """
        INSERT INTO budget_carryovers (
            carryover_uuid,
            budget_request_id,
            carryover_type_code,
            source_fiscal_year,
            destination_fiscal_year,
            authorized_limit_yen,
            actual_carryover_amount_yen,
            carryover_reason
        )
        VALUES (?, ?, 'MEIKYO', 2027, 2028,
                15000000, 12000000, '工事期間の変更')
        """,
        (str(uuid4()), request_id),
    ).lastrowid

    assert carryover_id > 0


def test_carryover_must_use_next_year(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_carryovers (
                carryover_uuid,
                budget_request_id,
                carryover_type_code,
                source_fiscal_year,
                destination_fiscal_year,
                carryover_reason
            )
            VALUES (?, ?, 'MEIKYO', 2027, 2029, '不正な年度')
            """,
            (str(uuid4()), request_id),
        )


def test_carryover_source_year_must_match_request(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_carryovers (
                carryover_uuid,
                budget_request_id,
                carryover_type_code,
                source_fiscal_year,
                destination_fiscal_year,
                carryover_reason
            )
            VALUES (?, ?, 'MEIKYO', 2028, 2029, '年度不一致')
            """,
            (str(uuid4()), request_id),
        )


def test_accident_carryover_requires_details(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_carryovers (
                carryover_uuid,
                budget_request_id,
                carryover_type_code,
                source_fiscal_year,
                destination_fiscal_year,
                carryover_reason
            )
            VALUES (?, ?, 'ACCIDENT', 2027, 2028,
                    '避け難い事故')
            """,
            (str(uuid4()), request_id),
        )


def test_accident_carryover_with_details(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    result = db.execute(
        """
        INSERT INTO budget_carryovers (
            carryover_uuid,
            budget_request_id,
            carryover_type_code,
            source_fiscal_year,
            destination_fiscal_year,
            carryover_reason,
            unavoidable_accident_description,
            obligation_incurred_on
        )
        VALUES (?, ?, 'ACCIDENT', 2027, 2028,
                '避け難い事故による工期延長',
                '想定外の重大な施工障害',
                '2027-10-01')
        """,
        (str(uuid4()), request_id),
    )

    assert result.lastrowid > 0


def test_carryover_cannot_exceed_authorized_limit(db):
    project_id = make_project(db)
    request_id = make_request(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_carryovers (
                carryover_uuid,
                budget_request_id,
                carryover_type_code,
                source_fiscal_year,
                destination_fiscal_year,
                authorized_limit_yen,
                actual_carryover_amount_yen,
                carryover_reason
            )
            VALUES (?, ?, 'MEIKYO', 2027, 2028,
                    10000000, 15000000, '限度額超過')
            """,
            (str(uuid4()), request_id),
        )


def test_obligation_year_schedule(db):
    project_id = make_project(db)
    obligation_id = make_obligation(db, project_id)

    for year, amount in (
        (2028, 30_000_000),
        (2029, 40_000_000),
        (2030, 20_000_000),
    ):
        db.execute(
            """
            INSERT INTO budget_obligation_years (
                obligation_id,
                fiscal_year,
                planned_payment_amount_yen
            )
            VALUES (?, ?, ?)
            """,
            (obligation_id, year, amount),
        )

    total = db.execute(
        """
        SELECT SUM(planned_payment_amount_yen)
        FROM budget_obligation_years
        WHERE obligation_id = ?
        """,
        (obligation_id,),
    ).fetchone()[0]

    assert total == 90_000_000


def test_obligation_year_outside_period_rejected(db):
    project_id = make_project(db)
    obligation_id = make_obligation(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_obligation_years (
                obligation_id,
                fiscal_year,
                planned_payment_amount_yen
            )
            VALUES (?, 2031, 1000000)
            """,
            (obligation_id,),
        )


def test_continuing_expenditure_years(db):
    project_id = make_project(db)
    continuing_id = make_continuing(db, project_id)

    for year, amount in (
        (2027, 20_000_000),
        (2028, 50_000_000),
        (2029, 50_000_000),
    ):
        db.execute(
            """
            INSERT INTO budget_continuing_years (
                continuing_id,
                fiscal_year,
                annual_amount_yen
            )
            VALUES (?, ?, ?)
            """,
            (continuing_id, year, amount),
        )

    total = db.execute(
        """
        SELECT SUM(annual_amount_yen)
        FROM budget_continuing_years
        WHERE continuing_id = ?
        """,
        (continuing_id,),
    ).fetchone()[0]

    assert total == 120_000_000


def test_continuing_year_outside_period_rejected(db):
    project_id = make_project(db)
    continuing_id = make_continuing(db, project_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO budget_continuing_years (
                continuing_id,
                fiscal_year,
                annual_amount_yen
            )
            VALUES (?, 2030, 1000000)
            """,
            (continuing_id,),
        )


def test_duplicate_continuing_year_rejected(db):
    project_id = make_project(db)
    continuing_id = make_continuing(db, project_id)

    sql = """
        INSERT INTO budget_continuing_years (
            continuing_id,
            fiscal_year,
            annual_amount_yen
        )
        VALUES (?, 2027, 20000000)
    """

    db.execute(sql, (continuing_id,))

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, (continuing_id,))


def test_database_integrity(db):
    project_id = make_project(db)
    obligation_id = make_obligation(db, project_id)

    db.execute(
        """
        INSERT INTO budget_obligation_years (
            obligation_id,
            fiscal_year,
            planned_payment_amount_yen
        )
        VALUES (?, 2028, 30000000)
        """,
        (obligation_id,),
    )

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
