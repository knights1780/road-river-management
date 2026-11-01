
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "maintenance_planning_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def add_plan(db, name="町内南側道路修繕計画"):
    cursor = db.execute(
        """
        INSERT INTO maintenance_plans (
            plan_uuid,
            plan_name,
            start_fiscal_year,
            end_fiscal_year
        )
        VALUES (?, ?, 2027, 2031)
        """,
        (str(uuid4()), name),
    )
    return cursor.lastrowid


def add_project(db, name):
    cursor = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name
        )
        VALUES (?, ?)
        """,
        (str(uuid4()), name),
    )
    return cursor.lastrowid


def add_stage(db, project_id, name, stage_type, weight):
    cursor = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid,
            project_id,
            stage_type_code,
            stage_name,
            progress_weight
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            project_id,
            stage_type,
            name,
            weight,
        ),
    )
    return cursor.lastrowid


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

    cursor = db.execute(
        """
        INSERT INTO managed_facilities (
            facility_uuid,
            facility_name,
            facility_type_id,
            facility_status_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid4()), name, type_id, status_id),
    )
    return cursor.lastrowid


def test_planning_tables_exist(db):
    expected = {
        "plan_statuses",
        "project_statuses",
        "stage_types",
        "stage_statuses",
        "workflow_transitions",
        "maintenance_plans",
        "maintenance_projects",
        "maintenance_plan_items",
        "project_facilities",
        "project_stages",
        "plan_progress_history",
        "project_progress_history",
        "progress_review_prompts",
    }

    actual = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert expected.issubset(actual)


def test_initial_masters(db):
    assert db.execute(
        "SELECT COUNT(*) FROM plan_statuses"
    ).fetchone()[0] == 8

    assert db.execute(
        "SELECT COUNT(*) FROM project_statuses"
    ).fetchone()[0] == 6

    assert db.execute(
        "SELECT COUNT(*) FROM stage_types"
    ).fetchone()[0] == 7

    assert db.execute(
        "SELECT COUNT(*) FROM stage_statuses"
    ).fetchone()[0] == 8

    assert db.execute(
        "SELECT COUNT(*) FROM workflow_transitions"
    ).fetchone()[0] > 0


def test_plan_contains_three_projects(db):
    plan_id = add_plan(db)

    names = [
        "1号道路修繕",
        "2号道路修繕",
        "3号道路修繕",
    ]

    for order, name in enumerate(names, start=1):
        project_id = add_project(db, name)
        db.execute(
            """
            INSERT INTO maintenance_plan_items (
                plan_id,
                project_id,
                display_order,
                progress_weight
            )
            VALUES (?, ?, ?, 1)
            """,
            (plan_id, project_id, order),
        )

    rows = db.execute(
        """
        SELECT p.project_name
        FROM maintenance_plan_items AS i
        JOIN maintenance_projects AS p
          ON p.project_id = i.project_id
        WHERE i.plan_id = ?
        ORDER BY i.display_order
        """,
        (plan_id,),
    ).fetchall()

    assert [row[0] for row in rows] == names


def test_duplicate_project_in_plan_rejected(db):
    plan_id = add_plan(db)
    project_id = add_project(db, "1号道路修繕")

    db.execute(
        """
        INSERT INTO maintenance_plan_items (
            plan_id, project_id
        )
        VALUES (?, ?)
        """,
        (plan_id, project_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO maintenance_plan_items (
                plan_id, project_id
            )
            VALUES (?, ?)
            """,
            (plan_id, project_id),
        )


def test_project_has_budget_design_construction(db):
    project_id = add_project(db, "1号道路修繕")

    add_stage(
        db, project_id, "予算要求", "BUDGET_REQUEST", 10
    )
    add_stage(
        db, project_id, "設計", "DESIGN", 30
    )
    add_stage(
        db, project_id, "工事", "CONSTRUCTION", 60
    )

    rows = db.execute(
        """
        SELECT stage_type_code, progress_weight
        FROM project_stages
        WHERE project_id = ?
        ORDER BY stage_id
        """,
        (project_id,),
    ).fetchall()

    assert rows == [
        ("BUDGET_REQUEST", 10),
        ("DESIGN", 30),
        ("CONSTRUCTION", 60),
    ]


def test_invalid_progress_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO maintenance_plans (
                plan_uuid,
                plan_name,
                start_fiscal_year,
                end_fiscal_year,
                official_progress_percent
            )
            VALUES (?, '不正計画', 2027, 2030, 120)
            """,
            (str(uuid4()),),
        )


def test_negative_weight_rejected(db):
    plan_id = add_plan(db)
    project_id = add_project(db, "1号道路修繕")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO maintenance_plan_items (
                plan_id,
                project_id,
                progress_weight
            )
            VALUES (?, ?, -1)
            """,
            (plan_id, project_id),
        )


def test_project_facility_link(db):
    project_id = add_project(db, "1号道路修繕")
    facility_id = add_facility(db, "1号道路")

    db.execute(
        """
        INSERT INTO project_facilities (
            project_id,
            facility_id
        )
        VALUES (?, ?)
        """,
        (project_id, facility_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO project_facilities (
                project_id,
                facility_id
            )
            VALUES (?, ?)
            """,
            (project_id, facility_id),
        )


def test_component_facility_mismatch_rejected(db):
    project_id = add_project(db, "設備更新")
    facility_a = add_facility(db, "施設A")
    facility_b = add_facility(db, "施設B")

    cursor = db.execute(
        """
        INSERT INTO facility_components (
            component_uuid,
            facility_id,
            component_code,
            component_name,
            component_type
        )
        VALUES (?, ?, 'EQ-01', '架空設備', 'EQUIPMENT')
        """,
        (str(uuid4()), facility_a),
    )
    component_id = cursor.lastrowid

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO project_facilities (
                project_id,
                facility_id,
                component_id
            )
            VALUES (?, ?, ?)
            """,
            (project_id, facility_b, component_id),
        )


def test_stage_history_project_mismatch_rejected(db):
    project_a = add_project(db, "事業A")
    project_b = add_project(db, "事業B")

    stage_id = add_stage(
        db, project_a, "工事", "CONSTRUCTION", 60
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO project_progress_history (
                project_id,
                stage_id,
                progress_scope,
                previous_progress_percent,
                new_progress_percent
            )
            VALUES (?, ?, 'STAGE', 0, 20)
            """,
            (project_b, stage_id),
        )


def test_duplicate_review_prompt_rejected(db):
    plan_id = add_plan(db)
    operation_uuid = str(uuid4())

    values = (
        str(uuid4()),
        operation_uuid,
        plan_id,
        "PLAN_MEMBERSHIP_CHANGED",
    )

    db.execute(
        """
        INSERT INTO progress_review_prompts (
            prompt_uuid,
            operation_uuid,
            plan_id,
            prompt_reason_code,
            official_progress_percent
        )
        VALUES (?, ?, ?, ?, 0)
        """,
        values,
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO progress_review_prompts (
                prompt_uuid,
                operation_uuid,
                plan_id,
                prompt_reason_code,
                official_progress_percent
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                str(uuid4()),
                operation_uuid,
                plan_id,
                "PLAN_MEMBERSHIP_CHANGED",
            ),
        )


def test_database_integrity(db):
    migrate.verify(db)
