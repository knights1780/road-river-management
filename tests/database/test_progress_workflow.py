
import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.progress_workflow import (
    resolve_plan_progress_review,
    update_stage_progress,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "progress_workflow_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def make_scenario(db, plan_count=1):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name
        )
        VALUES (?, '1号道路修繕')
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
        VALUES (?, ?, 'DESIGN', '設計',
                'IN_PROGRESS', 50, 30)
        """,
        (str(uuid4()), project_id),
    ).lastrowid

    plan_ids = []

    for index in range(plan_count):
        plan_id = db.execute(
            """
            INSERT INTO maintenance_plans (
                plan_uuid,
                plan_name,
                start_fiscal_year,
                end_fiscal_year,
                official_progress_percent
            )
            VALUES (?, ?, 2027, 2031, 40)
            """,
            (str(uuid4()), f"架空修繕計画{index + 1}"),
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

        plan_ids.append(plan_id)

    db.commit()
    return project_id, stage_id, plan_ids


def get_prompt(db, plan_id):
    return db.execute(
        """
        SELECT
            prompt_id,
            review_status_code,
            reference_progress_percent
        FROM progress_review_prompts
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()


def test_progress_change_creates_history_and_prompt(db):
    project_id, stage_id, plan_ids = make_scenario(db)

    result = update_stage_progress(db, stage_id, 80)

    assert result["project_id"] == project_id
    assert result["notified_plan_ids"] == plan_ids

    row = db.execute(
        """
        SELECT
            previous_progress_percent,
            new_progress_percent
        FROM project_progress_history
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()

    assert row == (50, 80)

    prompt = get_prompt(db, plan_ids[0])

    assert prompt[1] == "PENDING"
    assert prompt[2] == pytest.approx(80)


def test_small_progress_change_does_not_notify(db):
    _, stage_id, plan_ids = make_scenario(db)

    result = update_stage_progress(db, stage_id, 55)

    assert result["notified_plan_ids"] == []
    assert get_prompt(db, plan_ids[0]) is None

    count = db.execute(
        """
        SELECT COUNT(*)
        FROM project_progress_history
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()[0]

    assert count == 1


def test_change_notifies_multiple_plans(db):
    _, stage_id, plan_ids = make_scenario(
        db, plan_count=2
    )

    result = update_stage_progress(db, stage_id, 70)

    assert result["notified_plan_ids"] == plan_ids

    for plan_id in plan_ids:
        assert get_prompt(db, plan_id)[1] == "PENDING"


def test_invalid_transition_rolls_back(db):
    _, stage_id, plan_ids = make_scenario(db)

    with pytest.raises(ValueError, match="Invalid stage transition"):
        update_stage_progress(
            db,
            stage_id,
            90,
            new_status_code="NOT_STARTED",
        )

    progress = db.execute(
        """
        SELECT progress_percent
        FROM project_stages
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()[0]

    assert progress == 50
    assert get_prompt(db, plan_ids[0]) is None


def test_stop_requires_reason_and_confirmation(db):
    _, stage_id, plan_ids = make_scenario(db)

    with pytest.raises(ValueError, match="requires a reason"):
        update_stage_progress(
            db,
            stage_id,
            50,
            new_status_code="ON_HOLD",
            confirmed=True,
        )

    with pytest.raises(ValueError, match="requires confirmation"):
        update_stage_progress(
            db,
            stage_id,
            50,
            new_status_code="ON_HOLD",
            change_reason="協議が必要",
        )

    update_stage_progress(
        db,
        stage_id,
        50,
        new_status_code="ON_HOLD",
        change_reason="協議が必要",
        confirmed=True,
    )

    assert get_prompt(db, plan_ids[0])[1] == "PENDING"


def test_review_updates_official_progress(db):
    _, stage_id, plan_ids = make_scenario(db)

    update_stage_progress(db, stage_id, 80)
    prompt_id = get_prompt(db, plan_ids[0])[0]

    result = resolve_plan_progress_review(
        db,
        prompt_id,
        "REVIEWED_UPDATED",
        new_official_progress_percent=60,
        resolution_reason="設計進捗を反映",
    )

    assert result["current_official_progress_percent"] == 60

    official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_ids[0],),
    ).fetchone()[0]

    assert official == 60

    history = db.execute(
        """
        SELECT
            previous_progress_percent,
            new_progress_percent,
            reference_progress_percent
        FROM plan_progress_history
        WHERE plan_id = ?
        """,
        (plan_ids[0],),
    ).fetchone()

    assert history == (40, 60, 80)


def test_review_can_leave_official_unchanged(db):
    _, stage_id, plan_ids = make_scenario(db)

    update_stage_progress(db, stage_id, 80)
    prompt_id = get_prompt(db, plan_ids[0])[0]

    resolve_plan_progress_review(
        db,
        prompt_id,
        "REVIEWED_UNCHANGED",
        resolution_reason="工事未着手のため維持",
    )

    official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_ids[0],),
    ).fetchone()[0]

    assert official == 40

    count = db.execute(
        "SELECT COUNT(*) FROM plan_progress_history"
    ).fetchone()[0]

    assert count == 0


def test_resolved_prompt_cannot_be_resolved_twice(db):
    _, stage_id, plan_ids = make_scenario(db)

    update_stage_progress(db, stage_id, 80)
    prompt_id = get_prompt(db, plan_ids[0])[0]

    resolve_plan_progress_review(
        db,
        prompt_id,
        "REVIEWED_UNCHANGED",
    )

    with pytest.raises(ValueError, match="already resolved"):
        resolve_plan_progress_review(
            db,
            prompt_id,
            "REVIEWED_UNCHANGED",
        )


def test_invalid_progress_does_not_change_database(db):
    _, stage_id, plan_ids = make_scenario(db)

    with pytest.raises(ValueError):
        update_stage_progress(db, stage_id, 150)

    row = db.execute(
        """
        SELECT progress_percent
        FROM project_stages
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()

    assert row[0] == 50
    assert get_prompt(db, plan_ids[0]) is None


def test_foreign_key_integrity(db):
    make_scenario(db)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []


def test_database_rejects_invalid_history_reference(db):
    _, stage_id, _ = make_scenario(db)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO project_progress_history (
                project_id,
                stage_id,
                progress_scope,
                previous_progress_percent,
                new_progress_percent
            )
            VALUES (999999, ?, 'STAGE', 50, 80)
            """,
            (stage_id,),
        )
