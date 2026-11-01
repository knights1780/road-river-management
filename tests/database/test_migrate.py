
import sqlite3

import pytest

from road_river_management.database import migrate


@pytest.fixture
def migration_env(tmp_path, monkeypatch):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()

    monkeypatch.setattr(
        migrate,
        "MIGRATIONS_DIR",
        migration_dir,
    )

    db_path = tmp_path / "test.sqlite"

    connection = migrate.get_connection(db_path)
    migrate.ensure_schema_versions(connection)

    yield connection, migration_dir

    connection.close()


def write_migration(directory, name, sql):
    path = directory / name
    path.write_text(sql, encoding="utf-8")
    return path


def test_successful_migration(migration_env):
    connection, directory = migration_env

    write_migration(
        directory,
        "0001_test.sql",
        """
        CREATE TABLE test_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        """,
    )

    migrate.upgrade(connection)

    row = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'test_items'"
    ).fetchone()

    assert row is not None

    applied = migrate.get_applied_migrations(connection)

    assert 1 in applied


def test_multiple_statements(migration_env):
    connection, directory = migration_env

    write_migration(
        directory,
        "0001_multiple.sql",
        """
        CREATE TABLE test_items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );

        INSERT INTO test_items (name)
        VALUES ('first');

        INSERT INTO test_items (name)
        VALUES ('second');
        """,
    )

    migrate.upgrade(connection)

    result = connection.execute(
        "SELECT COUNT(*) FROM test_items"
    ).fetchone()[0]

    assert result == 2


def test_prevent_duplicate_migration(migration_env):
    connection, directory = migration_env

    write_migration(
        directory,
        "0001_test.sql",
        """
        CREATE TABLE test_items (
            id INTEGER PRIMARY KEY
        );
        """,
    )

    migrate.upgrade(connection)
    migrate.upgrade(connection)

    count = connection.execute(
        "SELECT COUNT(*) FROM schema_versions "
        "WHERE version_number = 1"
    ).fetchone()[0]

    assert count == 1


def test_rollback_on_sql_error(migration_env):
    connection, directory = migration_env

    write_migration(
        directory,
        "0001_invalid.sql",
        """
        CREATE TABLE rollback_test (
            id INTEGER PRIMARY KEY
        );

        INSERT INTO missing_table (id)
        VALUES (1);
        """,
    )

    with pytest.raises(sqlite3.OperationalError):
        migrate.upgrade(connection)

    row = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' "
        "AND name = 'rollback_test'"
    ).fetchone()

    assert row is None

    applied = migrate.get_applied_migrations(connection)

    assert 1 not in applied


def test_checksum_mismatch(migration_env):
    connection, directory = migration_env

    path = write_migration(
        directory,
        "0001_test.sql",
        """
        CREATE TABLE checksum_test (
            id INTEGER PRIMARY KEY
        );
        """,
    )

    migrate.upgrade(connection)

    path.write_text(
        """
        CREATE TABLE checksum_test (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="Checksum mismatch",
    ):
        migrate.upgrade(connection)


def test_database_verification(migration_env):
    connection, directory = migration_env

    write_migration(
        directory,
        "0001_test.sql",
        """
        CREATE TABLE parent (
            id INTEGER PRIMARY KEY
        );

        CREATE TABLE child (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL,
            FOREIGN KEY (parent_id)
                REFERENCES parent(id)
        );
        """,
    )

    migrate.upgrade(connection)

    migrate.verify(connection)
