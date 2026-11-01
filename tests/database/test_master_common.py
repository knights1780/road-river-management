
import sqlite3

import pytest

from road_river_management.database import migrate


@pytest.fixture
def master_db(tmp_path):
    db_path = tmp_path / "test_master.sqlite"

    connection = migrate.get_connection(db_path)
    migrate.ensure_schema_versions(connection)

    migration_path = (
        migrate.MIGRATIONS_DIR / "0002_master_common.sql"
    )

    migrate.apply_migration(
        connection,
        2,
        migration_path,
    )

    yield connection

    connection.close()


def test_initial_roles(master_db):
    roles = {
        row[0]
        for row in master_db.execute(
            "SELECT role_code FROM roles"
        )
    }

    assert roles == {
        "ADMINISTRATOR",
        "EDITOR",
        "VIEWER",
    }


def test_initial_importance_levels(master_db):
    rows = master_db.execute(
        """
        SELECT importance_code, priority_order
        FROM importance_levels
        ORDER BY priority_order
        """
    ).fetchall()

    assert rows == [
        ("CRITICAL", 1),
        ("HIGH", 2),
        ("NORMAL", 3),
        ("LOW", 4),
    ]


def test_duplicate_role_code_rejected(master_db):
    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            """
            INSERT INTO roles (role_code, role_name)
            VALUES ('ADMINISTRATOR', '重複管理者')
            """
        )


def test_invalid_active_status_rejected(master_db):
    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            """
            INSERT INTO departments (
                department_code,
                department_name,
                is_active
            )
            VALUES ('TEST', 'テスト課', 5)
            """
        )


def test_department_registration(master_db):
    master_db.execute(
        """
        INSERT INTO departments (
            department_code,
            department_name,
            sort_order
        )
        VALUES ('CONSTRUCTION', '建設課', 1)
        """
    )

    row = master_db.execute(
        """
        SELECT department_name
        FROM departments
        WHERE department_code = 'CONSTRUCTION'
        """
    ).fetchone()

    assert row[0] == "建設課"
