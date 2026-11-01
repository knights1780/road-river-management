
"""Tests for unified progress and construction transitions."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.construction_progress import (
    calculate_construction_plan_reference_progress,
    update_construction_progress,
)
from road_river_management.services.construction_transitions import (
    validate_construction_transition,
)
from road_river_management.services.progress import (
    calculate_plan_reference_progress,
    calculate_project_reference_progress,
)
from road_river_management.services.progress_reference import (
    calculate_plan_reference_progress as canonical_plan,
)
from road_river_management.services.progress_reference import (
    calculate_project_reference_progress as canonical_project,
)
from road_river_management.services.progress_reference import (
    calculate_stage_reference_progress,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "progress_integration_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def make_scenario(db):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name,
            official_progress_percent
        )
        VALUES (?, '架空修繕事業', 20)
        """,
        (str(uuid4()),),
    ).lastrowid

    stage_id = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid,
            project_id,
            stage_type_code,
            stage_name,
            status_code,
            progress_percent,
            progress_weight
        )
        VALUES (?, ?, 'CONSTRUCTION',
                '舗装修繕', 'IN_PROGRESS', 30, 100)
        """,
        (str(uuid4()), project_id),
    ).lastrowid

    plan_id = db.execute(
        """
        INSERT INTO maintenance_plans (
            plan_uuid,
            plan_name,
            start_fiscal_year,
            end_fiscal_year,
            official_progress_percent
        )
        VALUES (?, '架空長寿命化計画', 2027, 2031, 10)
        """,
        (str(uuid4()),),
    ).lastrowid

    db.execute(
        """
        INSERT INTO maintenance_plan_items (
            plan_id,
            project_id
        )
        VALUES (?, ?)
        """,
        (plan_id, project_id),
    )

    work_id = db.execute(
        """
        INSERT INTO construction_works (
            construction_uuid,
            construction_name,
            status_code,
            official_progress_percent
        )
        VALUES (?, '架空舗装工事', 'IN_PROGRESS', 60)
        """,
        (str(uuid4()),),
    ).lastrowid

    db.execute(
        """
        INSERT INTO project_construction_links (
            project_id,
            stage_id,
            construction_work_id,
            progress_weight
        )
        VALUES (?, ?, ?, 1)
        """,
        (project_id, stage_id, work_id),
    )

    db.commit()
    return project_id, stage_id, plan_id, work_id


def test_canonical_stage_reference_uses_work(db):
    _, stage_id, _, _ = make_scenario(db)

    assert calculate_stage_reference_progress(
        db, stage_id
    ) == pytest.approx(60)


def test_old_and_new_project_functions_agree(db):
    project_id, _, _, _ = make_scenario(db)

    assert calculate_project_reference_progress(
        db, project_id
    ) == pytest.approx(60)

    assert canonical_project(
        db, project_id
    ) == pytest.approx(60)


def test_all_plan_functions_agree(db):
    _, _, plan_id, _ = make_scenario(db)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(60)

    assert calculate_construction_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(60)

    assert canonical_plan(
        db, plan_id
    ) == pytest.approx(60)


def test_progress_update_reflects_in_all_calculations(db):
    project_id, _, plan_id, work_id = make_scenario(db)

    update_construction_progress(db, work_id, 80)

    assert calculate_project_reference_progress(
        db, project_id
    ) == pytest.approx(80)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(80)


def test_normal_construction_transition(db):
    _, _, _, work_id = make_scenario(db)

    result = update_construction_progress(
        db,
        work_id,
        60,
        new_status_code="ON_HOLD",
        change_reason="施工条件の再協議",
    )

    assert result["new_status_code"] == "ON_HOLD"


def test_invalid_construction_transition_rolls_back(db):
    _, _, _, work_id = make_scenario(db)

    with pytest.raises(
        ValueError,
        match="Invalid construction transition",
    ):
        update_construction_progress(
            db,
            work_id,
            90,
            new_status_code="SETTLED",
            change_reason="不正な状態変更",
        )

    row = db.execute(
        """
        SELECT status_code, official_progress_percent
        FROM construction_works
        WHERE construction_work_id = ?
        """,
        (work_id,),
    ).fetchone()

    assert row == ("IN_PROGRESS", 60)

    assert db.execute(
        "SELECT COUNT(*) FROM construction_status_history"
    ).fetchone()[0] == 0


def test_exceptional_transition_requires_confirmation():
    with pytest.raises(ValueError, match="requires confirmation"):
        validate_construction_transition(
            "COMPLETED",
            "IN_PROGRESS",
            change_reason="完成検査前の手直し",
        )

    assert validate_construction_transition(
        "COMPLETED",
        "IN_PROGRESS",
        change_reason="完成検査前の手直し",
        confirmed=True,
    ) is True


def test_transition_always_requires_reason():
    with pytest.raises(ValueError, match="requires a reason"):
        validate_construction_transition(
            "IN_PROGRESS",
            "COMPLETED",
        )


def test_official_parent_progress_remains_unchanged(db):
    project_id, stage_id, plan_id, work_id = make_scenario(db)

    update_construction_progress(db, work_id, 90)

    stage_progress = db.execute(
        """
        SELECT progress_percent
        FROM project_stages
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()[0]

    project_progress = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()[0]

    plan_progress = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]

    assert stage_progress == 30
    assert project_progress == 20
    assert plan_progress == 10
