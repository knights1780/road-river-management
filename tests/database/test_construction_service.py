
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.construction import (
    create_construction_work,
    get_construction_cost_summary,
    get_facility_construction_history,
    link_construction_to_project,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "construction_service_test.sqlite"
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
            facility_uuid,
            facility_name,
            facility_type_id,
            facility_status_id
        )
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid4()), name, type_id, status_id),
    ).lastrowid


def add_component(db, facility_id):
    return db.execute(
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
        (str(uuid4()), facility_id),
    ).lastrowid


def add_stage(db, stage_type="CONSTRUCTION"):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name
        )
        VALUES (?, '架空道路修繕事業')
        """,
        (str(uuid4()),),
    ).lastrowid

    stage_id = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid,
            project_id,
            stage_type_code,
            stage_name
        )
        VALUES (?, ?, ?, '工事工程')
        """,
        (str(uuid4()), project_id, stage_type),
    ).lastrowid

    db.commit()
    return project_id, stage_id


def test_create_work_for_three_roads(db):
    facilities = [
        add_facility(db, f"{i}号道路")
        for i in (1, 2, 3)
    ]
    db.commit()

    work_id = create_construction_work(
        db,
        "南側道路舗装修繕工事",
        facility_ids=facilities,
        current_contract_amount_yen=30_000_000,
    )

    linked = db.execute(
        """
        SELECT COUNT(*)
        FROM construction_work_facilities
        WHERE construction_work_id = ?
        """,
        (work_id,),
    ).fetchone()[0]

    assert linked == 3
    assert db.execute(
        "SELECT COUNT(*) FROM construction_works"
    ).fetchone()[0] == 1


def test_cost_is_not_multiplied_by_facility_count(db):
    facilities = [add_facility(db, str(i)) for i in range(3)]
    db.commit()

    create_construction_work(
        db,
        "3路線合同工事",
        facility_ids=facilities,
        current_contract_amount_yen=30_000_000,
    )

    summary = get_construction_cost_summary(db, facilities)

    assert summary["work_count"] == 1
    assert summary["total_contract_amount_yen"] == 30_000_000
    assert summary["unknown_amount_count"] == 0


def test_component_must_belong_to_selected_facility(db):
    facility_a = add_facility(db, "施設A")
    facility_b = add_facility(db, "施設B")
    component_id = add_component(db, facility_a)
    db.commit()

    with pytest.raises(ValueError, match="unselected facility"):
        create_construction_work(
            db,
            "設備更新",
            facility_ids=[facility_b],
            component_ids=[component_id],
        )

    assert db.execute(
        "SELECT COUNT(*) FROM construction_works"
    ).fetchone()[0] == 0


def test_create_work_with_component(db):
    facility_id = add_facility(db)
    component_id = add_component(db, facility_id)
    db.commit()

    work_id = create_construction_work(
        db,
        "ポンプ更新工事",
        facility_ids=[facility_id],
        component_ids=[component_id],
    )

    row = db.execute(
        """
        SELECT component_id
        FROM construction_work_components
        WHERE construction_work_id = ?
        """,
        (work_id,),
    ).fetchone()

    assert row == (component_id,)


def test_duplicate_facility_ids_are_rejected(db):
    facility_id = add_facility(db)
    db.commit()

    with pytest.raises(ValueError, match="duplicate"):
        create_construction_work(
            db,
            "重複対象工事",
            facility_ids=[facility_id, facility_id],
        )


def test_link_work_to_project_stage(db):
    facility_id = add_facility(db)
    db.commit()

    work_id = create_construction_work(
        db, "道路修繕工事", facility_ids=[facility_id]
    )
    project_id, stage_id = add_stage(db)

    link_id = link_construction_to_project(
        db, work_id, project_id, stage_id
    )

    row = db.execute(
        """
        SELECT construction_work_id
        FROM project_construction_links
        WHERE project_construction_link_id = ?
        """,
        (link_id,),
    ).fetchone()

    assert row == (work_id,)


def test_design_stage_cannot_link_work(db):
    facility_id = add_facility(db)
    db.commit()

    work_id = create_construction_work(
        db, "道路修繕工事", facility_ids=[facility_id]
    )
    project_id, stage_id = add_stage(db, "DESIGN")

    with pytest.raises(ValueError, match="Only CONSTRUCTION"):
        link_construction_to_project(
            db, work_id, project_id, stage_id
        )


def test_facility_history_shows_shared_and_legacy(db):
    facility_id = add_facility(db)
    db.commit()

    work_id = create_construction_work(
        db,
        "新方式舗装修繕",
        facility_ids=[facility_id],
        current_contract_amount_yen=2_000_000,
    )

    db.execute(
        """
        INSERT INTO facility_construction_history (
            construction_uuid,
            facility_id,
            project_name,
            contract_amount_yen
        )
        VALUES (?, ?, '旧方式橋梁補修', 1000000)
        """,
        (str(uuid4()), facility_id),
    )
    db.commit()

    records = get_facility_construction_history(
        db, facility_id
    )

    assert len(records) == 2
    assert {record["source"] for record in records} == {
        "SHARED",
        "LEGACY",
    }
    assert any(
        record["source"] == "SHARED"
        and record["record_id"] == work_id
        for record in records
    )


def test_unknown_cost_is_reported_separately(db):
    facility_id = add_facility(db)
    db.commit()

    create_construction_work(
        db, "契約額未確定工事", facility_ids=[facility_id]
    )

    summary = get_construction_cost_summary(
        db, [facility_id]
    )

    assert summary["work_count"] == 1
    assert summary["unknown_amount_count"] == 1
    assert summary["total_contract_amount_yen"] == 0


def test_failed_registration_rolls_back(db):
    facility_id = add_facility(db)
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        create_construction_work(
            db,
            "日付が不正な工事",
            facility_ids=[facility_id],
            planned_start_on="2029-04-01",
            planned_end_on="2028-03-31",
        )

    assert db.execute(
        "SELECT COUNT(*) FROM construction_works"
    ).fetchone()[0] == 0


def test_integrity(db):
    facility_id = add_facility(db)
    db.commit()

    create_construction_work(
        db, "整合性確認工事", facility_ids=[facility_id]
    )

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"
    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
