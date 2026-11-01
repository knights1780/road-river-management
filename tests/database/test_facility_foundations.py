
import json
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "facility_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def get_id(db, table, id_column, code_column, code):
    allowed = {
        "facility_types": ("facility_type_id", "type_code"),
        "facility_statuses": ("facility_status_id", "status_code"),
        "geometry_roles": ("geometry_role_id", "role_code"),
        "tags": ("tag_id", "tag_code"),
    }
    assert allowed[table] == (id_column, code_column)

    return db.execute(
        f"SELECT {id_column} FROM {table} "
        f"WHERE {code_column} = ?",
        (code,),
    ).fetchone()[0]


def add_facility(db, facility_uuid=None, type_code="ROAD"):
    type_id = get_id(
        db, "facility_types", "facility_type_id",
        "type_code", type_code
    )
    status_id = get_id(
        db, "facility_statuses", "facility_status_id",
        "status_code", "ACTIVE"
    )

    cursor = db.execute(
        """
        INSERT INTO managed_facilities (
            facility_uuid,
            facility_code,
            facility_name,
            facility_type_id,
            facility_status_id
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            facility_uuid or str(uuid4()),
            str(uuid4()),
            "架空市道テスト路線",
            type_id,
            status_id,
        ),
    )
    return cursor.lastrowid


def add_geometry(db, facility_id, is_primary=1, geometry_json=None):
    role_id = get_id(
        db, "geometry_roles", "geometry_role_id",
        "role_code", "FACILITY"
    )

    if geometry_json is None:
        geometry_json = json.dumps({
            "type": "LineString",
            "coordinates": [
                [138.0, 35.0],
                [138.001, 35.001],
            ],
        })

    db.execute(
        """
        INSERT INTO facility_geometries (
            facility_id,
            geometry_role_id,
            geometry_type,
            geometry_json,
            is_primary
        )
        VALUES (?, ?, 'LineString', ?, ?)
        """,
        (facility_id, role_id, geometry_json, is_primary),
    )


def test_initial_facility_categories_and_types(db):
    counts = dict(
        db.execute(
            """
            SELECT c.category_code, COUNT(t.facility_type_id)
            FROM facility_categories AS c
            LEFT JOIN facility_types AS t
              ON c.facility_category_id = t.facility_category_id
            GROUP BY c.facility_category_id
            """
        ).fetchall()
    )

    assert counts == {
        "ROAD": 3,
        "RIVER": 5,
        "PARK": 1,
        "AGRICULTURAL": 4,
    }


def test_facility_registration(db):
    facility_id = add_facility(db)

    row = db.execute(
        """
        SELECT facility_name
        FROM managed_facilities
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == ("架空市道テスト路線",)


def test_duplicate_facility_uuid_rejected(db):
    shared_uuid = str(uuid4())
    add_facility(db, facility_uuid=shared_uuid)

    with pytest.raises(sqlite3.IntegrityError):
        add_facility(db, facility_uuid=shared_uuid)


def test_invalid_facility_type_rejected(db):
    status_id = get_id(
        db, "facility_statuses",
        "facility_status_id", "status_code", "ACTIVE"
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO managed_facilities (
                facility_uuid,
                facility_name,
                facility_type_id,
                facility_status_id
            )
            VALUES (?, '不正な施設', 99999, ?)
            """,
            (str(uuid4()), status_id),
        )


def test_geometry_registration(db):
    facility_id = add_facility(db)
    add_geometry(db, facility_id)

    row = db.execute(
        """
        SELECT geometry_type, geometry_json
        FROM facility_geometries
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row[0] == "LineString"
    assert json.loads(row[1])["type"] == "LineString"


def test_invalid_geometry_json_rejected(db):
    facility_id = add_facility(db)

    with pytest.raises(sqlite3.IntegrityError):
        add_geometry(
            db,
            facility_id,
            geometry_json="{invalid json}",
        )


def test_duplicate_primary_geometry_rejected(db):
    facility_id = add_facility(db)
    add_geometry(db, facility_id, is_primary=1)

    with pytest.raises(sqlite3.IntegrityError):
        add_geometry(db, facility_id, is_primary=1)


def test_facility_tag_assignment(db):
    facility_id = add_facility(db)
    tag_id = get_id(
        db, "tags", "tag_id",
        "tag_code", "DISASTER_HISTORY"
    )

    db.execute(
        """
        INSERT INTO facility_tags (facility_id, tag_id)
        VALUES (?, ?)
        """,
        (facility_id, tag_id),
    )

    count = db.execute(
        """
        SELECT COUNT(*)
        FROM facility_tags
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()[0]

    assert count == 1


def test_duplicate_facility_tag_rejected(db):
    facility_id = add_facility(db)
    tag_id = get_id(
        db, "tags", "tag_id",
        "tag_code", "DISASTER_HISTORY"
    )

    db.execute(
        """
        INSERT INTO facility_tags (facility_id, tag_id)
        VALUES (?, ?)
        """,
        (facility_id, tag_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO facility_tags (facility_id, tag_id)
            VALUES (?, ?)
            """,
            (facility_id, tag_id),
        )


def test_facility_database_integrity(db):
    migrate.verify(db)
