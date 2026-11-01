
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.progress_workflow import (
    update_stage_progress,
)
from road_river_management.services.project_progress_workflow import (
    resolve_project_progress_review,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "project_review_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


def make_scenario(db):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name,
            official_progress_percent
        )
        VALUES (?, '1号道路修繕', 30)
        """,
        (str(uuid4()),),
    ).lastrowid

    stage_id = db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid, project_id,
            stage_type_code, stage_name,
            status_code, progress_percent,
            progress_weight
        )
        VALUES (?, ?, 'DESIGN', '設計',
                'IN_PROGRESS', 50, 30)
        """,
        (str(uuid4()), project_id),
    ).lastrowid

    plan_id = db.execute(
        """
        INSERT INTO maintenance_plans (
            plan_uuid, plan_name,
            start_fiscal_year, end_fiscal_year,
            official_progress_percent
        )
        VALUES (?, '南側道路修繕計画', 2027, 2031, 40)
        """,
        (str(uuid4()),),
    ).lastrowid

    db.execute(
        """
        INSERT INTO maintenance_plan_items (plan_id, project_id)
        VALUES (?, ?)
        """,
        (plan_id, project_id),
    )
    db.commit()
    return project_id, stage_id, plan_id


def project_prompt(db, project_id):
    return db.execute(
        """
        SELECT project_prompt_id, event_count,
               reference_progress_percent,
               review_status_code
        FROM project_progress_review_prompts
        WHERE project_id = ?
        ORDER BY project_prompt_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()


def test_stage_change_notifies_project_and_plan(db):
    project_id, stage_id, plan_id = make_scenario(db)

    update_stage_progress(db, stage_id, 80)

    prompt = project_prompt(db, project_id)
    assert prompt[1] == 1
    assert prompt[2] == pytest.approx(80)
    assert prompt[3] == "PENDING"

    count = db.execute(
        """
        SELECT COUNT(*) FROM progress_review_prompts
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]
    assert count == 1


def test_repeated_changes_are_consolidated(db):
    project_id, stage_id, plan_id = make_scenario(db)

    update_stage_progress(db, stage_id, 70)
    update_stage_progress(db, stage_id, 90)

    prompt = project_prompt(db, project_id)
    assert prompt[1] == 2
    assert prompt[2] == pytest.approx(90)

    plan_count = db.execute(
        """
        SELECT COUNT(*) FROM progress_review_prompts
        WHERE plan_id = ? AND review_status_code = 'PENDING'
        """,
        (plan_id,),
    ).fetchone()[0]

    assert plan_count == 1

    history_count = db.execute(
        """
        SELECT COUNT(*) FROM project_progress_history
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()[0]

    assert history_count == 2


def test_project_official_progress_review(db):
    project_id, stage_id, plan_id = make_scenario(db)
    update_stage_progress(db, stage_id, 80)

    prompt_id = project_prompt(db, project_id)[0]

    result = resolve_project_progress_review(
        db,
        prompt_id,
        "REVIEWED_UPDATED",
        new_official_progress_percent=60,
        resolution_reason="設計進捗を考慮",
    )

    assert result["current_official_progress_percent"] == 60
    assert result["notified_plan_ids"] == [plan_id]

    row = db.execute(
        """
        SELECT progress_scope,
               previous_progress_percent,
               new_progress_percent
        FROM project_progress_history
        WHERE project_id = ? AND progress_scope = 'PROJECT'
        """,
        (project_id,),
    ).fetchone()

    assert row == ("PROJECT", 30, 60)

    plan_official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]

    assert plan_official == 40


def test_project_review_without_change(db):
    project_id, stage_id, _ = make_scenario(db)
    update_stage_progress(db, stage_id, 80)

    prompt_id = project_prompt(db, project_id)[0]

    result = resolve_project_progress_review(
        db,
        prompt_id,
        "REVIEWED_UNCHANGED",
        resolution_reason="工事未着手のため維持",
    )

    assert result["current_official_progress_percent"] == 30
    assert result["notified_plan_ids"] == []
    assert project_prompt(db, project_id)[3] == "REVIEWED_UNCHANGED"


def test_resolved_prompt_cannot_be_reused(db):
    project_id, stage_id, _ = make_scenario(db)
    update_stage_progress(db, stage_id, 80)

    prompt_id = project_prompt(db, project_id)[0]
    resolve_project_progress_review(
        db, prompt_id, "REVIEWED_UNCHANGED"
    )

    with pytest.raises(ValueError, match="already resolved"):
        resolve_project_progress_review(
            db, prompt_id, "REVIEWED_UNCHANGED"
        )


def test_next_change_creates_new_prompt(db):
    project_id, stage_id, _ = make_scenario(db)
    update_stage_progress(db, stage_id, 70)

    first_id = project_prompt(db, project_id)[0]
    resolve_project_progress_review(
        db, first_id, "REVIEWED_UNCHANGED"
    )

    update_stage_progress(db, stage_id, 90)

    latest = project_prompt(db, project_id)
    assert latest[0] != first_id
    assert latest[1] == 1
    assert latest[3] == "PENDING"


def test_invalid_official_progress_rolls_back(db):
    project_id, stage_id, _ = make_scenario(db)
    update_stage_progress(db, stage_id, 80)

    prompt_id = project_prompt(db, project_id)[0]

    with pytest.raises(ValueError):
        resolve_project_progress_review(
            db,
            prompt_id,
            "REVIEWED_UPDATED",
            new_official_progress_percent=150,
        )

    assert project_prompt(db, project_id)[3] == "PENDING"


def test_database_integrity(db):
    make_scenario(db)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
