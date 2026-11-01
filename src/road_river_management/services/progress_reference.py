
"""Canonical reference progress calculations.

All calculations are read-only.
Official progress values are never modified.
"""

import sqlite3

from road_river_management.services.progress import (
    calculate_stage_progress,
)


def calculate_stage_reference_progress(
    connection: sqlite3.Connection,
    stage_id: int,
) -> float | None:
    """Return the stage reference, using linked works when present."""

    stage = connection.execute(
        """
        SELECT stage_type_code, progress_percent
        FROM project_stages
        WHERE stage_id = ?
        """,
        (stage_id,),
    ).fetchone()

    if stage is None:
        raise ValueError(f"Stage not found: {stage_id}")

    stage_type, stage_progress = stage

    if stage_type != "CONSTRUCTION":
        return float(stage_progress)

    works = connection.execute(
        """
        SELECT
            w.official_progress_percent,
            link.progress_weight
        FROM project_construction_links AS link
        JOIN construction_works AS w
          ON w.construction_work_id = link.construction_work_id
        WHERE link.stage_id = ?
        ORDER BY link.project_construction_link_id
        """,
        (stage_id,),
    ).fetchall()

    if not works:
        return float(stage_progress)

    return calculate_stage_progress(
        [(float(progress), float(weight)) for progress, weight in works]
    )


def calculate_project_reference_progress(
    connection: sqlite3.Connection,
    project_id: int,
) -> float | None:
    """Calculate the project reference from included stages."""

    exists = connection.execute(
        """
        SELECT 1
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()

    if exists is None:
        raise ValueError(f"Project not found: {project_id}")

    stages = connection.execute(
        """
        SELECT stage_id, progress_weight
        FROM project_stages
        WHERE project_id = ?
          AND is_included_in_progress = 1
          AND progress_weight > 0
        ORDER BY stage_order, stage_id
        """,
        (project_id,),
    ).fetchall()

    values = []

    for stage_id, weight in stages:
        reference = calculate_stage_reference_progress(
            connection,
            stage_id,
        )

        if reference is None:
            return None

        values.append((reference, float(weight)))

    return calculate_stage_progress(values)


def calculate_plan_reference_progress(
    connection: sqlite3.Connection,
    plan_id: int,
) -> float | None:
    """Calculate plan reference from included projects."""

    exists = connection.execute(
        """
        SELECT 1
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()

    if exists is None:
        raise ValueError(f"Plan not found: {plan_id}")

    projects = connection.execute(
        """
        SELECT project_id, progress_weight
        FROM maintenance_plan_items
        WHERE plan_id = ?
          AND is_included_in_progress = 1
          AND excluded_on IS NULL
          AND progress_weight > 0
        ORDER BY display_order, plan_item_id
        """,
        (plan_id,),
    ).fetchall()

    values = []

    for project_id, weight in projects:
        reference = calculate_project_reference_progress(
            connection,
            project_id,
        )

        if reference is None:
            return None

        values.append((reference, float(weight)))

    return calculate_stage_progress(values)
