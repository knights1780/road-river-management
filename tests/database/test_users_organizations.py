
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "test_users.sqlite"
    )
    migrate.ensure_schema_versions(connection)

    for version, filename in [
        (2, "0002_master_common.sql"),
        (3, "0003_master_extensions.sql"),
        (4, "0004_users_organizations.sql"),
    ]:
        migrate.apply_migration(
            connection,
            version,
            migrate.MIGRATIONS_DIR / filename,
        )

    yield connection
    connection.close()


def add_user(db, login_id="testuser"):
    cursor = db.execute(
        """
        INSERT INTO users (
            user_uuid, login_id, display_name,
            password_hash, password_algorithm
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            login_id,
            "テスト職員",
            "dummy-hash-for-schema-test",
            "TEST_ONLY",
        ),
    )
    return cursor.lastrowid


def test_tables_exist(db):
    expected = {
        "users",
        "user_roles",
        "user_departments",
        "organizations",
        "organization_contacts",
        "user_login_history",
    }
    actual = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert expected.issubset(actual)


def test_user_registration(db):
    user_id = add_user(db)
    row = db.execute(
        "SELECT login_id FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    assert row == ("testuser",)


def test_login_id_unique_case_insensitive(db):
    add_user(db, "TestUser")
    with pytest.raises(sqlite3.IntegrityError):
        add_user(db, "testuser")


def test_local_password_hash_required(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO users (
                user_uuid, login_id, display_name
            )
            VALUES (?, 'nohash', 'ハッシュなし')
            """,
            (str(uuid4()),),
        )


def test_user_role_assignment(db):
    user_id = add_user(db)
    role_id = db.execute(
        "SELECT role_id FROM roles WHERE role_code='EDITOR'"
    ).fetchone()[0]

    db.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (user_id, role_id),
    )

    count = db.execute(
        "SELECT COUNT(*) FROM user_roles WHERE user_id=?",
        (user_id,),
    ).fetchone()[0]
    assert count == 1


def test_duplicate_active_role_rejected(db):
    user_id = add_user(db)
    role_id = db.execute(
        "SELECT role_id FROM roles WHERE role_code='VIEWER'"
    ).fetchone()[0]

    db.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
        (user_id, role_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, role_id),
        )


def test_department_transfer_history(db):
    user_id = add_user(db)

    for code, name in [
        ("CONSTRUCTION", "建設課"),
        ("GENERAL", "総務課"),
    ]:
        db.execute(
            """
            INSERT INTO departments (
                department_code, department_name
            ) VALUES (?, ?)
            """,
            (code, name),
        )

    department_ids = dict(
        db.execute(
            "SELECT department_code, department_id FROM departments"
        ).fetchall()
    )

    db.execute(
        """
        INSERT INTO user_departments (
            user_id, department_id, started_at, ended_at
        )
        VALUES (?, ?, '2025-04-01', '2026-03-31')
        """,
        (user_id, department_ids["CONSTRUCTION"]),
    )

    db.execute(
        """
        INSERT INTO user_departments (
            user_id, department_id, started_at
        )
        VALUES (?, ?, '2026-04-01')
        """,
        (user_id, department_ids["GENERAL"]),
    )

    rows = db.execute(
        """
        SELECT d.department_code, ud.started_at, ud.ended_at
        FROM user_departments AS ud
        JOIN departments AS d
          ON ud.department_id = d.department_id
        WHERE ud.user_id = ?
        ORDER BY ud.started_at
        """,
        (user_id,),
    ).fetchall()

    assert rows == [
        ("CONSTRUCTION", "2025-04-01", "2026-03-31"),
        ("GENERAL", "2026-04-01", None),
    ]


def test_organization_contact_relationship(db):
    type_id = db.execute(
        """
        SELECT organization_type_id
        FROM organization_types
        WHERE type_code='COMPANY'
        """
    ).fetchone()[0]

    cursor = db.execute(
        """
        INSERT INTO organizations (
            organization_uuid, organization_name,
            organization_type_id
        )
        VALUES (?, '架空建設株式会社', ?)
        """,
        (str(uuid4()), type_id),
    )

    organization_id = cursor.lastrowid

    db.execute(
        """
        INSERT INTO organization_contacts (
            organization_id, contact_name, is_primary
        )
        VALUES (?, '架空担当者', 1)
        """,
        (organization_id,),
    )

    row = db.execute(
        """
        SELECT o.organization_name, c.contact_name
        FROM organization_contacts AS c
        JOIN organizations AS o
          ON c.organization_id = o.organization_id
        """
    ).fetchone()

    assert row == ("架空建設株式会社", "架空担当者")
