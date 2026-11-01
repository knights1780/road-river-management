
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "construction_works_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def add_facility(db, name="架空道路"):
    type_id = db.execute(
        """
        SELECT facility_type_id
        FROM facility_types
        WHERE type_code = 'ROAD'
        """
    ).fetchone()[0]
    status_id = db.execute(
        """
        SELECT facility_status_id
        FROM facility_statuses
        WHERE status_code = 'ACTIVE'
        """
    ).fetchone()[0]

    return db.execute(
        """
        INSERT INTO managed_facilities (
            facility_uuid, facility_name,
            facility_type_id, facility_status_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid4()), name, type_id, status_id),
    ).lastrowid


def add_work(db, name="架空舗装修繕工事", amount=30000000):
    return db.execute(
        """
        INSERT INTO construction_works (
            construction_uuid,
            construction_name,
            current_contract_amount_yen
        )
        VALUES (?, ?, ?)
        """,
        (str(uuid4()), name, amount),
    ).lastrowid


def add_project_and_stage(db, stage_type="CONSTRUCTION"):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name
        )
        VALUES (?, '架空修繕事業')
        """,
        (str(uuid4()),),
    ).lastrowid

    stage_id = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid, project_id,
            stage_type_code, stage_name,
            progress_weight
        )
        VALUES (?, ?, ?, '工程', 60)
        """,
        (str(uuid4()), project_id, stage_type),
    ).lastrowid

    return project_id, stage_id


def add_component(db, facility_id):
    return db.execute(
        """
        INSERT INTO facility_components (
            component_uuid, facility_id,
            component_code, component_name,
            component_type
        )
        VALUES (?, ?, 'EQ-001', '架空ポンプ', 'PUMP')
        """,
        (str(uuid4()), facility_id),
    ).lastrowid


def test_construction_tables_exist(db):
    expected = {
        "construction_statuses",
        "construction_works",
        "construction_work_facilities",
        "construction_work_components",
        "project_construction_links",
        "construction_status_history",
        "construction_change_history",
    }
    actual = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert expected.issubset(actual)


def test_initial_construction_statuses(db):
    rows = db.execute(
        "SELECT status_code FROM construction_statuses"
    ).fetchall()
    assert len(rows) == 10
    assert ("IN_PROGRESS",) in rows
    assert ("INSPECTED",) in rows


def test_one_work_three_facilities_no_cost_duplication(db):
    work_id = add_work(db)

    for name in ("1号道路", "2号道路", "3号道路"):
        facility_id = add_facility(db, name)
        db.execute(
            """
            INSERT INTO construction_work_facilities (
                construction_work_id, facility_id
            )
            VALUES (?, ?)
            """,
            (work_id, facility_id),
        )

    rows = db.execute(
        """
        SELECT f.facility_name
        FROM construction_work_facilities AS wf
        JOIN managed_facilities AS f
          ON f.facility_id = wf.facility_id
        WHERE wf.construction_work_id = ?
        ORDER BY f.facility_name
        """,
        (work_id,),
    ).fetchall()
    assert len(rows) == 3

    total = db.execute(
        """
        SELECT SUM(current_contract_amount_yen)
        FROM construction_works
        WHERE construction_work_id IN (
            SELECT DISTINCT construction_work_id
            FROM construction_work_facilities
        )
        """
    ).fetchone()[0]
    assert total == 30000000


def test_duplicate_work_facility_rejected(db):
    work_id = add_work(db)
    facility_id = add_facility(db)

    db.execute(
        """
        INSERT INTO construction_work_facilities (
            construction_work_id, facility_id
        )
        VALUES (?, ?)
        """,
        (work_id, facility_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO construction_work_facilities (
                construction_work_id, facility_id
            )
            VALUES (?, ?)
            """,
            (work_id, facility_id),
        )


def test_component_requires_linked_facility(db):
    work_id = add_work(db)
    facility_id = add_facility(db)
    component_id = add_component(db, facility_id)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO construction_work_components (
                construction_work_id, component_id
            )
            VALUES (?, ?)
            """,
            (work_id, component_id),
        )

    db.execute(
        """
        INSERT INTO construction_work_facilities (
            construction_work_id, facility_id
        )
        VALUES (?, ?)
        """,
        (work_id, facility_id),
    )

    db.execute(
        """
        INSERT INTO construction_work_components (
            construction_work_id, component_id
        )
        VALUES (?, ?)
        """,
        (work_id, component_id),
    )


def test_linked_component_blocks_facility_unlink(db):
    work_id = add_work(db)
    facility_id = add_facility(db)
    component_id = add_component(db, facility_id)

    db.execute(
        """
        INSERT INTO construction_work_facilities (
            construction_work_id, facility_id
        )
        VALUES (?, ?)
        """,
        (work_id, facility_id),
    )
    db.execute(
        """
        INSERT INTO construction_work_components (
            construction_work_id, component_id
        )
        VALUES (?, ?)
        """,
        (work_id, component_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            DELETE FROM construction_work_facilities
            WHERE construction_work_id = ? AND facility_id = ?
            """,
            (work_id, facility_id),
        )


def test_project_construction_link(db):
    project_id, stage_id = add_project_and_stage(db)
    work_id = add_work(db)

    db.execute(
        """
        INSERT INTO project_construction_links (
            project_id, stage_id,
            construction_work_id
        )
        VALUES (?, ?, ?)
        """,
        (project_id, stage_id, work_id),
    )

    row = db.execute(
        """
        SELECT construction_work_id
        FROM project_construction_links
        WHERE project_id = ? AND stage_id = ?
        """,
        (project_id, stage_id),
    ).fetchone()
    assert row == (work_id,)


def test_design_stage_cannot_link_construction(db):
    project_id, stage_id = add_project_and_stage(db, "DESIGN")
    work_id = add_work(db)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO project_construction_links (
                project_id, stage_id,
                construction_work_id
            )
            VALUES (?, ?, ?)
            """,
            (project_id, stage_id, work_id),
        )


def test_negative_contract_amount_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        add_work(db, amount=-100)


def test_construction_status_history(db):
    work_id = add_work(db)

    db.execute(
        """
        INSERT INTO construction_status_history (
            construction_work_id,
            previous_status_code,
            new_status_code,
            previous_progress_percent,
            new_progress_percent,
            change_reason
        )
        VALUES (?, 'PLANNED', 'PREPARING', 0, 10, '発注準備')
        """,
        (work_id,),
    )

    row = db.execute(
        """
        SELECT previous_status_code, new_status_code
        FROM construction_status_history
        WHERE construction_work_id = ?
        """,
        (work_id,),
    ).fetchone()
    assert row == ("PLANNED", "PREPARING")


def test_construction_change_history(db):
    work_id = add_work(db)

    db.execute(
        """
        INSERT INTO construction_change_history (
            construction_work_id,
            change_number,
            change_type_code,
            changed_on,
            previous_value,
            new_value,
            change_reason
        )
        VALUES (?, 1, 'CONTRACT_AMOUNT',
                '2028-09-01', '30000000',
                '32000000', '設計変更')
        """,
        (work_id,),
    )

    row = db.execute(
        """
        SELECT previous_value, new_value
        FROM construction_change_history
        WHERE construction_work_id = ?
        """,
        (work_id,),
    ).fetchone()
    assert row == ("30000000", "32000000")


def test_database_integrity(db):
    migrate.verify(db)
