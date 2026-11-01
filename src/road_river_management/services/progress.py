
"""Reference progress calculations for maintenance plans.

All database operations in this module are read-only.
Official progress values are never updated automatically.
"""

import math
import sqlite3
from collections.abc import Iterable
from typing import Any


def _validate_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    result = float(value)

    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")

    return result


def calculate_stage_progress(
    items: Iterable[tuple[float, float]],
) -> float | None:
    """Return a weighted progress percentage, or None if unassessable.

    Each item is (progress_percent, progress_weight).
    Zero-weight entries do not contribute to the result.
    """

    weighted_sum = 0.0
    weight_sum = 0.0

    for percent, weight in items:
        percent = _validate_number(percent, "progress_percent")
        weight = _validate_number(weight, "progress_weight")

        if not 0 <= percent <= 100:
            raise ValueError("progress_percent must be between 0 and 100")

        if weight < 0:
            raise ValueError("progress_weight must be non-negative")

        if weight > 0:
            weighted_sum += percent * weight
            weight_sum += weight

    if weight_sum == 0:
        return None

    result = weighted_sum / weight_sum

    if not math.isfinite(result):
        raise ValueError("weighted progress calculation overflowed")

    return result


def _get_project_row(
    connection: sqlite3.Connection,
    project_id: int,
) -> tuple[Any, ...]:
    row = connection.execute(
        """
        SELECT
            project_id,
            project_name,
            status_code,
            official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Project not found: {project_id}")

    return tuple(row)


def _get_project_stages(
    connection: sqlite3.Connection,
    project_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            stage_id,
            stage_name,
            stage_type_code,
            status_code,
            progress_percent,
            progress_weight,
            is_included_in_progress
        FROM project_stages
        WHERE project_id = ?
        ORDER BY stage_order, stage_id
        """,
        (project_id,),
    ).fetchall()

    return [
        {
            "stage_id": row[0],
            "stage_name": row[1],
            "stage_type_code": row[2],
            "status_code": row[3],
            "progress_percent": row[4],
            "progress_weight": row[5],
            "is_included_in_progress": bool(row[6]),
        }
        for row in rows
    ]


def calculate_project_reference_progress(
    connection: sqlite3.Connection,
    project_id: int,
) -> float | None:
    """Calculate one project's progress from included stages."""

    _get_project_row(connection, project_id)
    stages = _get_project_stages(connection, project_id)

    return calculate_stage_progress(
        (
            stage["progress_percent"],
            stage["progress_weight"],
        )
        for stage in stages
        if stage["is_included_in_progress"]
    )


def _get_plan_projects(
    connection: sqlite3.Connection,
    plan_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            item.plan_item_id,
            item.project_id,
            item.display_order,
            item.progress_weight,
            item.is_included_in_progress,
            item.excluded_on
        FROM maintenance_plan_items AS item
        WHERE item.plan_id = ?
        ORDER BY item.display_order, item.plan_item_id
        """,
        (plan_id,),
    ).fetchall()

    return [
        {
            "plan_item_id": row[0],
            "project_id": row[1],
            "display_order": row[2],
            "progress_weight": row[3],
            "is_included_in_progress": bool(row[4]),
            "excluded_on": row[5],
        }
        for row in rows
    ]


def _get_plan_row(
    connection: sqlite3.Connection,
    plan_id: int,
) -> tuple[Any, ...]:
    row = connection.execute(
        """
        SELECT
            plan_id,
            plan_name,
            status_code,
            official_progress_percent,
            start_fiscal_year,
            end_fiscal_year
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Plan not found: {plan_id}")

    return tuple(row)


def calculate_plan_reference_progress(
    connection: sqlite3.Connection,
    plan_id: int,
) -> float | None:
    """Calculate plan progress from its included projects.

    A project with no calculable stage progress makes an included,
    positive-weight plan incomplete for reference calculation.
    """

    _get_plan_row(connection, plan_id)
    items = _get_plan_projects(connection, plan_id)

    weighted_items = []

    for item in items:
        if (
            not item["is_included_in_progress"]
            or item["excluded_on"] is not None
            or item["progress_weight"] == 0
        ):
            continue

        project_progress = calculate_project_reference_progress(
            connection,
            item["project_id"],
        )

        if project_progress is None:
            return None

        weighted_items.append(
            (project_progress, item["progress_weight"])
        )

    return calculate_stage_progress(weighted_items)


def get_plan_progress_summary(
    connection: sqlite3.Connection,
    plan_id: int,
) -> dict[str, Any]:
    """Return plan, project, and stage progress for a future UI."""

    plan = _get_plan_row(connection, plan_id)
    items = _get_plan_projects(connection, plan_id)
    projects = []

    for item in items:
        project = _get_project_row(
            connection,
            item["project_id"],
        )

        reference_progress = calculate_project_reference_progress(
            connection,
            item["project_id"],
        )

        projects.append(
            {
                "project_id": project[0],
                "project_name": project[1],
                "status_code": project[2],
                "official_progress_percent": project[3],
                "reference_progress_percent": reference_progress,
                "progress_weight": item["progress_weight"],
                "is_included_in_progress": (
                    item["is_included_in_progress"]
                    and item["excluded_on"] is None
                ),
                "stages": _get_project_stages(
                    connection,
                    item["project_id"],
                ),
            }
        )

    return {
        "plan_id": plan[0],
        "plan_name": plan[1],
        "status_code": plan[2],
        "official_progress_percent": plan[3],
        "reference_progress_percent": (
            calculate_plan_reference_progress(
                connection,
                plan_id,
            )
        ),
        "start_fiscal_year": plan[4],
        "end_fiscal_year": plan[5],
        "projects": projects,
    }
