
"""Tests for construction-linked reference progress."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.construction_progress import (
    calculate_construction_plan_reference_progress,
    calculate_construction_project_reference_progress,
    calculate_construction_stage_reference_progress,
    update_construction_progress,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "construction_progress_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_scenario(db, work_progresses=(50.0,)):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name,
            official_progress_percent
        )
        VALUES (?, '架空道路修繕事業', 20)
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
        VALUES (?, ?, 'CONSTRUCTION', '舗装工事',
                'IN_PROGRESS', 30, 100)
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
        VALUES (?, '架空道路長寿命化計画', 2027, 2031, 10)
        """,
        (str(uuid4()),),
    ).lastrowid

    db.execute(
        """
        INSERT INTO maintenance_plan_items (
            plan_id, project_id
        )
        VALUES (?, ?)
        """,
        (plan_id, project_id),
    )

    work_ids = []

    for index, progress in enumerate(work_progresses):
        work_id = db.execute(
            """
            INSERT INTO construction_works (
                construction_uuid,
                construction_name,
                status_code,
                official_progress_percent
            )
            VALUES (?, ?, 'IN_PROGRESS', ?)
            """,
            (str(uuid4()), f"架空工事{index + 1}", progress),
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

        work_ids.append(work_id)

    db.commit()
    return project_id, stage_id, plan_id, work_ids


def count_history(db):
    return db.execute(
        "SELECT COUNT(*) FROM construction_status_history"
    ).fetchone()[0]


def test_reference_progress_uses_construction_work(db):
    project_id, stage_id, plan_id, _ = make_scenario(db, (60,))

    assert calculate_construction_stage_reference_progress(
        db, stage_id
    ) == pytest.approx(60)

    assert calculate_construction_project_reference_progress(
        db, project_id
    ) == pytest.approx(60)

    assert calculate_construction_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(60)


def test_multiple_works_have_weighted_average(db):
    project_id, stage_id, _, work_ids = make_scenario(
        db, (80, 20)
    )

    db.execute(
        """
        UPDATE project_construction_links
        SET progress_weight = 3
        WHERE construction_work_id = ?
        """,
        (work_ids[0],),
    )
    db.commit()

    assert calculate_construction_stage_reference_progress(
        db, stage_id
    ) == pytest.approx(65)

    assert calculate_construction_project_reference_progress(
        db, project_id
    ) == pytest.approx(65)


def test_work_update_creates_history_and_prompts(db):
    project_id, stage_id, plan_id, work_ids = make_scenario(db)

    result = update_construction_progress(
        db, work_ids[0], 80
    )

    assert result["notified_project_ids"] == [project_id]
    assert result["notified_plan_ids"] == [plan_id]
    assert count_history(db) == 1

    stage_reference = (
        calculate_construction_stage_reference_progress(
            db, stage_id
        )
    )
    assert stage_reference == pytest.approx(80)

    project_prompt = db.execute(
        """
        SELECT reference_progress_percent
        FROM project_progress_review_prompts
        WHERE project_id = ?
          AND review_status_code = 'PENDING'
        """,
        (project_id,),
    ).fetchone()

    assert project_prompt[0] == pytest.approx(80)

    plan_prompt = db.execute(
        """
        SELECT reference_progress_percent
        FROM progress_review_prompts
        WHERE plan_id = ?
          AND review_status_code = 'PENDING'
        """,
        (plan_id,),
    ).fetchone()

    assert plan_prompt[0] == pytest.approx(80)


def test_small_change_writes_history_without_prompt(db):
    project_id, _, plan_id, work_ids = make_scenario(db)

    result = update_construction_progress(
        db, work_ids[0], 55
    )

    assert result["notified_project_ids"] == []
    assert result["notified_plan_ids"] == []
    assert count_history(db) == 1

    project_prompt_count = db.execute(
        """
        SELECT COUNT(*)
        FROM project_progress_review_prompts
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()[0]

    plan_prompt_count = db.execute(
        """
        SELECT COUNT(*)
        FROM progress_review_prompts
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]

    assert project_prompt_count == 0
    assert plan_prompt_count == 0


def test_repeated_updates_consolidate_prompts(db):
    project_id, _, plan_id, work_ids = make_scenario(db)

    update_construction_progress(db, work_ids[0], 70)
    update_construction_progress(db, work_ids[0], 90)

    prompt = db.execute(
        """
        SELECT event_count, reference_progress_percent
        FROM project_progress_review_prompts
        WHERE project_id = ?
          AND review_status_code = 'PENDING'
        """,
        (project_id,),
    ).fetchone()

    assert prompt[0] == 2
    assert prompt[1] == pytest.approx(90)
    assert count_history(db) == 2

    pending_plans = db.execute(
        """
        SELECT COUNT(*)
        FROM progress_review_prompts
        WHERE plan_id = ?
          AND review_status_code = 'PENDING'
        """,
        (plan_id,),
    ).fetchone()[0]

    assert pending_plans == 1


def test_official_progress_is_not_automatically_changed(db):
    project_id, stage_id, plan_id, work_ids = make_scenario(db)

    update_construction_progress(db, work_ids[0], 90)

    stage_official = db.execute(
        """
        SELECT progress_percent
        FROM project_stages
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()[0]

    project_official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()[0]

    plan_official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]

    assert stage_official == 30
    assert project_official == 20
    assert plan_official == 10


def test_invalid_progress_does_not_modify_database(db):
    _, _, _, work_ids = make_scenario(db)

    with pytest.raises(ValueError):
        update_construction_progress(db, work_ids[0], 150)

    assert count_history(db) == 0

    progress = db.execute(
        """
        SELECT official_progress_percent
        FROM construction_works
        WHERE construction_work_id = ?
        """,
        (work_ids[0],),
    ).fetchone()[0]

    assert progress == 50


def test_status_change_requires_reason(db):
    _, _, _, work_ids = make_scenario(db)

    with pytest.raises(ValueError, match="requires a reason"):
        update_construction_progress(
            db,
            work_ids[0],
            50,
            new_status_code="ON_HOLD",
        )

    result = update_construction_progress(
        db,
        work_ids[0],
        50,
        new_status_code="ON_HOLD",
        change_reason="施工条件の再協議",
    )

    assert result["new_status_code"] == "ON_HOLD"
    assert count_history(db) == 1


def test_database_integrity(db):
    make_scenario(db)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
