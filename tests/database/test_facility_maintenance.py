
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "maintenance_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def add_facility(db, type_code="BRIDGE"):
    type_id = db.execute(
        """
        SELECT facility_type_id
        FROM facility_types
        WHERE type_code = ?
        """,
        (type_code,),
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
        (
            str(uuid4()),
            "架空テスト施設",
            type_id,
            status_id,
        ),
    )
    return cursor.lastrowid


def test_maintenance_tables_exist(db):
    expected = {
        "facility_inspections",
        "facility_repairs",
        "facility_construction_history",
        "facility_disaster_history",
        "component_maintenance_history",
    }

    actual = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert expected.issubset(actual)


def test_bridge_inspection_registration(db):
    facility_id = add_facility(db)

    db.execute(
        """
        INSERT INTO facility_inspections (
            inspection_uuid,
            facility_id,
            inspection_date,
            inspection_type,
            inspection_method,
            condition_rating,
            next_inspection_due_date
        )
        VALUES (?, ?, '2026-06-01', 'STATUTORY',
                'DIRECT', 'II', '2031-06-01')
        """,
        (str(uuid4()), facility_id),
    )

    row = db.execute(
        """
        SELECT inspection_method,
               condition_rating,
               next_inspection_due_date
        FROM facility_inspections
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == ("DIRECT", "II", "2031-06-01")


def test_invalid_inspection_due_date(db):
    facility_id = add_facility(db)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO facility_inspections (
                inspection_uuid,
                facility_id,
                inspection_date,
                inspection_type,
                inspection_method,
                next_inspection_due_date
            )
            VALUES (?, ?, '2026-06-01', 'STATUTORY',
                    'DIRECT', '2025-06-01')
            """,
            (str(uuid4()), facility_id),
        )


def test_facility_repair_registration(db):
    facility_id = add_facility(db, "ROAD")

    db.execute(
        """
        INSERT INTO facility_repairs (
            repair_uuid,
            facility_id,
            repair_name,
            repair_type,
            started_on,
            completed_on,
            actual_cost_yen
        )
        VALUES (?, ?, '舗装修繕', 'PLANNED',
                '2026-04-01', '2026-04-30', 1500000)
        """,
        (str(uuid4()), facility_id),
    )

    row = db.execute(
        """
        SELECT repair_name, actual_cost_yen
        FROM facility_repairs
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == ("舗装修繕", 1500000)


def test_negative_repair_cost_rejected(db):
    facility_id = add_facility(db, "ROAD")

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO facility_repairs (
                repair_uuid,
                facility_id,
                repair_name,
                repair_type,
                actual_cost_yen
            )
            VALUES (?, ?, '不正な修繕', 'PLANNED', -100)
            """,
            (str(uuid4()), facility_id),
        )


def test_construction_history(db):
    facility_id = add_facility(db)

    db.execute(
        """
        INSERT INTO facility_construction_history (
            construction_uuid,
            facility_id,
            project_name,
            started_on,
            completed_on,
            contract_amount_yen,
            final_amount_yen
        )
        VALUES (?, ?, '架空橋補修工事',
                '2026-07-01', '2026-09-30',
                5000000, 5200000)
        """,
        (str(uuid4()), facility_id),
    )

    row = db.execute(
        """
        SELECT contract_amount_yen, final_amount_yen
        FROM facility_construction_history
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == (5000000, 5200000)


def test_disaster_history(db):
    facility_id = add_facility(db, "ROAD")

    db.execute(
        """
        INSERT INTO facility_disaster_history (
            disaster_uuid,
            facility_id,
            occurred_on,
            disaster_type,
            damage_description,
            damage_severity
        )
        VALUES (?, ?, '2026-09-01',
                'TYPHOON', '路肩損傷', 'MODERATE')
        """,
        (str(uuid4()), facility_id),
    )

    row = db.execute(
        """
        SELECT disaster_type, damage_severity
        FROM facility_disaster_history
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    assert row == ("TYPHOON", "MODERATE")


def test_component_maintenance(db):
    facility_id = add_facility(db, "PUMP_STATION")

    cursor = db.execute(
        """
        INSERT INTO facility_components (
            component_uuid,
            facility_id,
            component_code,
            component_name,
            component_type
        )
        VALUES (?, ?, 'PUMP-01', '1号ポンプ', 'PUMP')
        """,
        (str(uuid4()), facility_id),
    )

    component_id = cursor.lastrowid

    db.execute(
        """
        INSERT INTO component_maintenance_history (
            maintenance_uuid,
            component_id,
            maintenance_date,
            maintenance_type,
            maintenance_name,
            cost_yen
        )
        VALUES (?, ?, '2026-05-20',
                'OVERHAUL', 'ポンプ分解整備', 800000)
        """,
        (str(uuid4()), component_id),
    )

    row = db.execute(
        """
        SELECT maintenance_name, cost_yen
        FROM component_maintenance_history
        WHERE component_id = ?
        """,
        (component_id,),
    ).fetchone()

    assert row == ("ポンプ分解整備", 800000)


def test_invalid_facility_reference(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO facility_inspections (
                inspection_uuid,
                facility_id,
                inspection_date,
                inspection_type,
                inspection_method
            )
            VALUES (?, 999999, '2026-01-01',
                    'PERIODIC', 'DIRECT')
            """,
            (str(uuid4()),),
        )


def test_database_integrity(db):
    migrate.verify(db)
