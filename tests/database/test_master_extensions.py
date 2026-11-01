
import sqlite3

import pytest

from road_river_management.database import migrate


@pytest.fixture
def master_db(tmp_path):
    db_path = tmp_path / "test_extensions.sqlite"
    connection = migrate.get_connection(db_path)
    migrate.ensure_schema_versions(connection)

    migration_path = (
        migrate.MIGRATIONS_DIR
        / "0003_master_extensions.sql"
    )

    migrate.apply_migration(
        connection,
        3,
        migration_path,
    )

    yield connection
    connection.close()


def test_master_tables_exist(master_db):
    expected = {
        "organization_types",
        "document_types",
        "reminder_statuses",
        "tag_groups",
        "tags",
        "geometry_roles",
        "process_results",
        "operation_types",
    }

    actual = {
        row[0]
        for row in master_db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table'"
        )
    }

    assert expected.issubset(actual)


def test_initial_record_counts(master_db):
    expected = {
        "organization_types": 5,
        "document_types": 6,
        "reminder_statuses": 4,
        "tag_groups": 4,
        "tags": 4,
        "geometry_roles": 4,
        "process_results": 5,
        "operation_types": 7,
    }

    for table, count in expected.items():
        actual = master_db.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

        assert actual == count


def test_duplicate_tag_code_rejected(master_db):
    group_id = master_db.execute(
        "SELECT tag_group_id FROM tag_groups LIMIT 1"
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            """
            INSERT INTO tags (
                tag_group_id,
                tag_code,
                tag_name
            )
            VALUES (?, 'DISASTER_HISTORY', '重複タグ')
            """,
            (group_id,),
        )


def test_invalid_tag_group_rejected(master_db):
    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            """
            INSERT INTO tags (
                tag_group_id,
                tag_code,
                tag_name
            )
            VALUES (99999, 'INVALID_GROUP', '不正なタグ')
            """
        )


def test_invalid_active_status_rejected(master_db):
    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            """
            INSERT INTO document_types (
                type_code,
                type_name,
                is_active
            )
            VALUES ('INVALID', '不正値', 2)
            """
        )


def test_tag_group_relationship(master_db):
    rows = master_db.execute(
        """
        SELECT t.tag_code, g.group_code
        FROM tags AS t
        JOIN tag_groups AS g
          ON t.tag_group_id = g.tag_group_id
        ORDER BY t.tag_code
        """
    ).fetchall()

    assert len(rows) == 4

    assert (
        "DISASTER_HISTORY",
        "DISASTER",
    ) in rows

    assert (
        "INSPECTION_REQUIRED",
        "INSPECTION",
    ) in rows


def test_tag_group_delete_restricted(master_db):
    group_id = master_db.execute(
        """
        SELECT tag_group_id
        FROM tag_groups
        WHERE group_code = 'DISASTER'
        """
    ).fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError):
        master_db.execute(
            "DELETE FROM tag_groups WHERE tag_group_id = ?",
            (group_id,),
        )


def test_database_integrity(master_db):
    migrate.verify(master_db)
