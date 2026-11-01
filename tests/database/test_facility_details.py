
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "facility_details_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection

    connection.close()


def master_id(db, table, id_column, code_column, code):
    allowed = {
        "facility_types": ("facility_type_id", "type_code"),
        "facility_statuses": ("facility_status_id", "status_code"),
        "organization_types": (
            "organization_type_id", "type_code"
        ),
    }

    assert allowed[table] == (id_column, code_column)

    row = db.execute(
        f"SELECT {id_column} FROM {table} "
        f"WHERE {code_column} = ?",
        (code,),
    ).fetchone()

    assert row is not None
    return row[0]


def add_facility(db, type_code, name="架空施設"):
    type_id = master_id(
        db, "facility_types",
        "facility_type_id", "type_code", type_code
    )
    status_id = master_id(
        db, "facility_statuses",
        "facility_status_id", "status_code", "ACTIVE"
    )

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


def add_component(
    db,
    facility_id,
    name,
    code,
    parent_id=None,
):
    cursor = db.execute(
        """
        INSERT INTO facility_components (
            component_uuid,
            facility_id,
            parent_component_id,
            component_code,
            component_name,
            component_type
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            facility_id,
            parent_id,
            code,
            name,
            "EQUIPMENT",
        ),
    )
    return cursor.lastrowid


def test_detail_tables_exist(db):
    expected = {
        "road_details",
        "bridge_details",
        "river_details",
        "park_details",
        "agricultural_facility_details",
        "facility_components",
    }

    actual = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    assert expected.issubset(actual)


def test_road_details_registration(db):
    facility_id = add_facility(db, "ROAD", "架空市道A号線")

    db.execute(
        """
        INSERT INTO road_details (
            facility_id,
            route_number,
            road_classification,
            is_school_route,
            road_width_m
        )
        VALUES (?, 'A-001', 'MUNICIPAL', 1, 5.5)
        """,
        (facility_id,),
    )

    result = db.execute(
        """
        SELECT road_classification,
               is_school_route,
               road_width_m
        FROM road_details
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert result == ("MUNICIPAL", 1, 5.5)


def test_negative_road_width_rejected(db):
    facility_id = add_facility(db, "ROAD")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO road_details (
                facility_id,
                road_width_m
            )
            VALUES (?, -3.0)
            """,
            (facility_id,),
        )


def test_bridge_direct_inspection(db):
    facility_id = add_facility(db, "BRIDGE", "架空橋A")

    db.execute(
        """
        INSERT INTO bridge_details (
            facility_id,
            bridge_number,
            bridge_length_m,
            construction_year,
            is_direct_inspection
        )
        VALUES (?, 'BR-001', 25.5, 1995, 1)
        """,
        (facility_id,),
    )

    result = db.execute(
        """
        SELECT bridge_length_m,
               is_direct_inspection,
               inspection_interval_years
        FROM bridge_details
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert result == (25.5, 1, 5)


def test_invalid_bridge_length_rejected(db):
    facility_id = add_facility(db, "BRIDGE")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO bridge_details (
                facility_id,
                bridge_length_m
            )
            VALUES (?, -1)
            """,
            (facility_id,),
        )


def test_river_and_park_details(db):
    river_id = add_facility(db, "ORDINARY_RIVER")
    park_id = add_facility(db, "CITY_PARK")

    db.execute(
        """
        INSERT INTO river_details (
            facility_id,
            river_system_name,
            river_length_m
        )
        VALUES (?, '架空水系', 1200)
        """,
        (river_id,),
    )

    db.execute(
        """
        INSERT INTO park_details (
            facility_id,
            park_area_sqm,
            opened_on
        )
        VALUES (?, 3500, '2000-04-01')
        """,
        (park_id,),
    )

    river_length = db.execute(
        "SELECT river_length_m FROM river_details "
        "WHERE facility_id = ?",
        (river_id,),
    ).fetchone()[0]

    park_area = db.execute(
        "SELECT park_area_sqm FROM park_details "
        "WHERE facility_id = ?",
        (park_id,),
    ).fetchone()[0]

    assert river_length == 1200
    assert park_area == 3500


def test_agricultural_operator_relationship(db):
    type_id = master_id(
        db, "organization_types",
        "organization_type_id", "type_code", "GOVERNMENT"
    )

    cursor = db.execute(
        """
        INSERT INTO organizations (
            organization_uuid,
            organization_name,
            organization_type_id
        )
        VALUES (?, '架空管理組織', ?)
        """,
        (str(uuid4()), type_id),
    )
    organization_id = cursor.lastrowid

    facility_id = add_facility(
        db, "DRAINAGE_STATION", "架空排水機場"
    )

    db.execute(
        """
        INSERT INTO agricultural_facility_details (
            facility_id,
            facility_function,
            operating_organization_id
        )
        VALUES (?, '排水', ?)
        """,
        (facility_id, organization_id),
    )

    row = db.execute(
        """
        SELECT o.organization_name
        FROM agricultural_facility_details AS a
        JOIN organizations AS o
          ON a.operating_organization_id = o.organization_id
        WHERE a.facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == ("架空管理組織",)


def test_component_hierarchy(db):
    facility_id = add_facility(
        db, "PUMP_STATION", "架空ポンプ場"
    )

    parent_id = add_component(
        db, facility_id, "ポンプ設備", "PUMP-SYSTEM"
    )

    child_id = add_component(
        db,
        facility_id,
        "1号ポンプ",
        "PUMP-01",
        parent_id,
    )

    row = db.execute(
        """
        SELECT p.component_name, c.component_name
        FROM facility_components AS c
        JOIN facility_components AS p
          ON c.parent_component_id = p.component_id
        WHERE c.component_id = ?
        """,
        (child_id,),
    ).fetchone()

    assert row == ("ポンプ設備", "1号ポンプ")


def test_component_self_reference_rejected(db):
    facility_id = add_facility(db, "PUMP_STATION")

    component_id = add_component(
        db, facility_id, "テスト設備", "TEST-01"
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            UPDATE facility_components
            SET parent_component_id = ?
            WHERE component_id = ?
            """,
            (component_id, component_id),
        )


def test_duplicate_component_code_rejected(db):
    facility_id = add_facility(db, "PUMP_STATION")

    add_component(
        db, facility_id, "1号ポンプ", "PUMP-01"
    )

    with pytest.raises(sqlite3.IntegrityError):
        add_component(
            db, facility_id, "別のポンプ", "PUMP-01"
        )


def test_invalid_facility_reference_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO bridge_details (
                facility_id,
                bridge_number
            )
            VALUES (999999, 'INVALID')
            """
        )


def test_facility_details_integrity(db):
    migrate.verify(db)
