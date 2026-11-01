import argparse
import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations" / "sqlite"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "development.sqlite"

MIGRATION_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def now_iso():
    return datetime.now(UTC).isoformat()


def get_connection(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")

    return connection


def ensure_schema_versions(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            schema_version_id INTEGER PRIMARY KEY,
            version_number INTEGER NOT NULL UNIQUE,
            migration_name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            application_version TEXT,
            result_status TEXT NOT NULL DEFAULT 'SUCCESS',
            remarks TEXT
        )
    """)
    connection.commit()


def get_migrations():
    migrations = []

    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = MIGRATION_PATTERN.match(path.name)

        if match:
            migrations.append((int(match.group(1)), path))

    return sorted(migrations)


def calculate_checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_applied_migrations(connection):
    rows = connection.execute("""
        SELECT version_number, checksum
        FROM schema_versions
        ORDER BY version_number
    """).fetchall()

    return dict(rows)


def show_status(connection):
    applied = get_applied_migrations(connection)

    for version, path in get_migrations():
        checksum = calculate_checksum(path)

        if version not in applied:
            status = "PENDING"
        elif applied[version] == checksum:
            status = "APPLIED"
        else:
            status = "CHECKSUM MISMATCH"

        print(f"{version:04d} {path.name}: {status}")



def apply_migration(connection, version, path):
    sql = path.read_text(encoding="utf-8")
    checksum = calculate_checksum(path)

    try:
        connection.execute("BEGIN IMMEDIATE")

        statement_buffer = ""

        for line in sql.splitlines(keepends=True):
            statement_buffer += line

            if sqlite3.complete_statement(statement_buffer):
                statement = statement_buffer.strip()

                if statement:
                    connection.execute(statement)

                statement_buffer = ""

        if statement_buffer.strip():
            raise RuntimeError(
                f"Incomplete SQL statement: {path.name}"
            )

        connection.execute("""
            INSERT INTO schema_versions (
                version_number,
                migration_name,
                checksum,
                applied_at,
                application_version,
                result_status
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            version,
            path.name,
            checksum,
            now_iso(),
            "0.1.0",
            "SUCCESS"
        ))

        connection.commit()
        print(f"Applied: {path.name}")

    except Exception:
        connection.rollback()
        raise



def upgrade(connection):
    applied = get_applied_migrations(connection)

    for version, path in get_migrations():
        checksum = calculate_checksum(path)

        if version in applied:
            if applied[version] != checksum:
                raise RuntimeError(
                    f"Checksum mismatch: {path.name}"
                )
            continue

        apply_migration(connection, version, path)


def verify(connection):
    applied = get_applied_migrations(connection)

    for version, path in get_migrations():
        if version in applied and applied[version] != calculate_checksum(path):
            raise RuntimeError(
                f"Checksum mismatch: {path.name}"
            )

    foreign_key_errors = connection.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if foreign_key_errors:
        raise RuntimeError(
            f"Foreign key errors: {foreign_key_errors}"
        )

    integrity_result = connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity_result != "ok":
        raise RuntimeError(integrity_result)

    print("Database verification successful.")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=["status", "upgrade", "verify"]
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH
    )

    args = parser.parse_args()

    connection = get_connection(args.db)

    try:
        ensure_schema_versions(connection)

        if args.command == "status":
            show_status(connection)

        elif args.command == "upgrade":
            upgrade(connection)

        elif args.command == "verify":
            verify(connection)

    finally:
        connection.close()


if __name__ == "__main__":
    main()
