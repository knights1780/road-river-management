
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.progress import (
    calculate_plan_reference_progress,
    calculate_project_reference_progress,
    calculate_stage_progress,
    get_plan_progress_summary,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "progress_service_test.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)

    yield connection
    connection.close()


def add_plan(db, official_progress=40):
    cursor = db.execute(
        """
        INSERT INTO maintenance_plans (
            plan_uuid,
            plan_name,
            start_fiscal_year,
            end_fiscal_year,
            official_progress_percent
        )
        VALUES (?, '町内南側道路修繕計画', 2027, 2031, ?)
        """,
        (str(uuid4()), official_progress),
    )
    return cursor.lastrowid


def add_project(db, name, official_progress=0):
    cursor = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid,
            project_name,
            official_progress_percent
        )
        VALUES (?, ?, ?)
        """,
        (str(uuid4()), name, official_progress),
    )
    return cursor.lastrowid


def add_plan_item(
    db,
    plan_id,
    project_id,
    weight=1,
    included=True,
):
    db.execute(
        """
        INSERT INTO maintenance_plan_items (
            plan_id,
            project_id,
            progress_weight,
            is_included_in_progress
        )
        VALUES (?, ?, ?, ?)
        """,
        (plan_id, project_id, weight, int(included)),
    )


def add_stage(
    db,
    project_id,
    stage_type,
    percent,
    weight,
    included=True,
):
    db.execute(
        """
        INSERT INTO project_stages (
            stage_uuid,
            project_id,
            stage_type_code,
            stage_name,
            progress_percent,
            progress_weight,
            is_included_in_progress
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            project_id,
            stage_type,
            stage_type,
            percent,
            weight,
            int(included),
        ),
    )


def add_three_stages(db, project_id, values):
    for stage_type, percent, weight in zip(
        ("BUDGET_REQUEST", "DESIGN", "CONSTRUCTION"),
        values,
        (10, 30, 60),
        strict=True,
    ):
        add_stage(db, project_id, stage_type, percent, weight)


def test_weighted_progress():
    assert calculate_stage_progress(
        [(100, 10), (100, 30), (50, 60)]
    ) == pytest.approx(70)


def test_zero_weights_are_unassessable():
    assert calculate_stage_progress(
        [(100, 0), (50, 0)]
    ) is None


def test_empty_items_are_unassessable():
    assert calculate_stage_progress([]) is None


@pytest.mark.parametrize(
    "items",
    [
        [(120, 1)],
        [(-1, 1)],
        [(50, -1)],
        [(float("nan"), 1)],
        [(50, float("inf"))],
    ],
)
def test_invalid_progress_or_weight(items):
    with pytest.raises(ValueError):
        calculate_stage_progress(items)


def test_project_reference_progress(db):
    project_id = add_project(db, "1号道路修繕")
    add_three_stages(db, project_id, (100, 100, 50))

    assert calculate_project_reference_progress(
        db, project_id
    ) == pytest.approx(70)


def test_project_without_stages_is_unassessable(db):
    project_id = add_project(db, "未着手事業")

    assert calculate_project_reference_progress(
        db, project_id
    ) is None


def test_excluded_stage_not_counted(db):
    project_id = add_project(db, "工程除外テスト")

    add_stage(
        db, project_id, "DESIGN", 100, 30
    )
    add_stage(
        db, project_id, "CONSTRUCTION", 0, 60,
        included=False,
    )

    assert calculate_project_reference_progress(
        db, project_id
    ) == pytest.approx(100)


def test_three_road_plan_progress(db):
    plan_id = add_plan(db)

    cases = (
        ("1号道路修繕", (100, 100, 50)),
        ("2号道路修繕", (100, 50, 0)),
        ("3号道路修繕", (50, 0, 0)),
    )

    for name, progress_values in cases:
        project_id = add_project(db, name)
        add_plan_item(db, plan_id, project_id)
        add_three_stages(db, project_id, progress_values)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(100 / 3)

    summary = get_plan_progress_summary(db, plan_id)

    assert summary["plan_name"] == "町内南側道路修繕計画"
    assert summary["official_progress_percent"] == 40
    assert summary["reference_progress_percent"] == pytest.approx(
        100 / 3
    )
    assert len(summary["projects"]) == 3
    assert [
        project["reference_progress_percent"]
        for project in summary["projects"]
    ] == pytest.approx([70, 25, 5])

    assert all(
        len(project["stages"]) == 3
        for project in summary["projects"]
    )


def test_plan_with_missing_project_progress(db):
    plan_id = add_plan(db)

    project_a = add_project(db, "1号道路修繕")
    project_b = add_project(db, "2号道路修繕")

    add_plan_item(db, plan_id, project_a)
    add_plan_item(db, plan_id, project_b)

    add_stage(db, project_a, "DESIGN", 100, 30)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) is None


def test_excluded_project_not_counted(db):
    plan_id = add_plan(db)

    project_a = add_project(db, "対象事業")
    project_b = add_project(db, "除外事業")

    add_plan_item(db, plan_id, project_a)
    add_plan_item(
        db, plan_id, project_b, included=False
    )

    add_stage(db, project_a, "DESIGN", 80, 30)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(80)


def test_zero_weight_project_not_counted(db):
    plan_id = add_plan(db)

    project_a = add_project(db, "対象事業")
    project_b = add_project(db, "重みゼロ事業")

    add_plan_item(db, plan_id, project_a, weight=2)
    add_plan_item(db, plan_id, project_b, weight=0)

    add_stage(db, project_a, "DESIGN", 60, 30)

    assert calculate_plan_reference_progress(
        db, plan_id
    ) == pytest.approx(60)


def test_official_progress_not_modified(db):
    plan_id = add_plan(db, official_progress=45)
    project_id = add_project(
        db, "1号道路修繕", official_progress=30
    )

    add_plan_item(db, plan_id, project_id)
    add_stage(db, project_id, "DESIGN", 100, 30)

    db.commit()

    before_changes = db.total_changes

    summary = get_plan_progress_summary(db, plan_id)

    after_changes = db.total_changes

    plan_official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()[0]

    project_official = db.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()[0]

    assert summary["reference_progress_percent"] == 100
    assert plan_official == 45
    assert project_official == 30
    assert before_changes == after_changes


def test_unknown_plan_rejected(db):
    with pytest.raises(ValueError, match="Plan not found"):
        calculate_plan_reference_progress(db, 999999)


def test_database_integrity(db):
    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
