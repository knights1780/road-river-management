
"""Maintenance plan progress calculation and summary services.

Reference progress is calculated from stages and linked construction works.
Official progress is managed separately and is never modified here.
"""

import math
import sqlite3
from collections.abc import Iterable


def _validate_number(value: float, name: str) -> float:
    """Validate a numeric value and reject non-finite numbers."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")

    result = float(value)

    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")

    return result


def calculate_stage_progress(
    items: Iterable[tuple[float, float]],
) -> float | None:
    """Calculate weighted average progress.

    Args:
        items:
            Iterable of (progress_percent, progress_weight).

    Returns:
        Weighted average between 0 and 100.
        None if there are no items with positive weights.

    Notes:
        Zero-weight items do not contribute to the average.
        Progress must be between 0 and 100.
        Weights must be non-negative and finite.
    """

    weighted_total = 0.0
    total_weight = 0.0

    for progress, weight in items:
        progress_value = _validate_number(
            progress,
            "progress",
        )
        weight_value = _validate_number(
            weight,
            "weight",
        )

        if not 0 <= progress_value <= 100:
            raise ValueError(
                "progress must be between 0 and 100"
            )

        if weight_value < 0:
            raise ValueError(
                "weight must be non-negative"
            )

        if weight_value == 0:
            continue

        weighted_total += progress_value * weight_value
        total_weight += weight_value

    if total_weight == 0:
        return None

    result = weighted_total / total_weight

    if not math.isfinite(result):
        raise ValueError(
            "calculated progress must be finite"
        )

    return result


def calculate_project_reference_progress(
    connection: sqlite3.Connection,
    project_id: int,
) -> float | None:
    """Calculate project progress using the canonical service.

    Construction stages use linked construction works when available.
    Other stages use their recorded progress percentages.
    """

    from road_river_management.services.progress_reference import (
        calculate_project_reference_progress as calculate_reference,
    )

    return calculate_reference(connection, project_id)


def calculate_plan_reference_progress(
    connection: sqlite3.Connection,
    plan_id: int,
) -> float | None:
    """Calculate plan progress using the canonical service.

    Included projects are combined using their progress weights.
    """

    from road_river_management.services.progress_reference import (
        calculate_plan_reference_progress as calculate_reference,
    )

    return calculate_reference(connection, plan_id)


def get_plan_progress_summary(
    connection: sqlite3.Connection,
    plan_id: int,
) -> dict:
    """Return hierarchical progress information for a maintenance plan.

    The summary includes:
        - Plan official and reference progress
        - Included and excluded maintenance projects
        - Project official and reference progress
        - Stage recorded and reference progress
        - Stage weights and inclusion flags

    This function performs SELECT queries only.
    """

    from road_river_management.services.progress_reference import (
        calculate_stage_reference_progress,
    )

    plan = connection.execute(
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

    if plan is None:
        raise ValueError(
            f"Plan not found: {plan_id}"
        )

    (
        db_plan_id,
        plan_name,
        plan_status,
        plan_official_progress,
        start_fiscal_year,
        end_fiscal_year,
    ) = plan

    plan_reference_progress = (
        calculate_plan_reference_progress(
            connection,
            plan_id,
        )
    )

    project_rows = connection.execute(
        """
        SELECT
            item.plan_item_id,
            item.project_id,
            item.progress_weight,
            item.is_included_in_progress,
            item.excluded_on,
            item.display_order,
            project.project_name,
            project.status_code,
            project.official_progress_percent
        FROM maintenance_plan_items AS item
        JOIN maintenance_projects AS project
          ON project.project_id = item.project_id
        WHERE item.plan_id = ?
        ORDER BY
            item.display_order,
            item.plan_item_id
        """,
        (plan_id,),
    ).fetchall()

    projects = []

    for project_row in project_rows:
        (
            plan_item_id,
            project_id,
            project_weight,
            project_included,
            excluded_on,
            display_order,
            project_name,
            project_status,
            project_official_progress,
        ) = project_row

        project_reference_progress = (
            calculate_project_reference_progress(
                connection,
                project_id,
            )
        )

        stage_rows = connection.execute(
            """
            SELECT
                stage_id,
                stage_name,
                stage_type_code,
                status_code,
                progress_percent,
                progress_weight,
                is_included_in_progress,
                stage_order
            FROM project_stages
            WHERE project_id = ?
            ORDER BY
                stage_order,
                stage_id
            """,
            (project_id,),
        ).fetchall()

        stages = []

        for stage_row in stage_rows:
            (
                stage_id,
                stage_name,
                stage_type_code,
                stage_status,
                stage_progress,
                stage_weight,
                stage_included,
                stage_order,
            ) = stage_row

            stage_reference_progress = (
                calculate_stage_reference_progress(
                    connection,
                    stage_id,
                )
            )

            stages.append(
                {
                    "stage_id": stage_id,
                    "stage_name": stage_name,
                    "stage_type_code": stage_type_code,
                    "status_code": stage_status,
                    "progress_percent": stage_progress,
                    "reference_progress_percent": (
                        stage_reference_progress
                    ),
                    "progress_weight": stage_weight,
                    "is_included_in_progress": bool(
                        stage_included
                    ),
                    "stage_order": stage_order,
                }
            )

        projects.append(
            {
                "plan_item_id": plan_item_id,
                "project_id": project_id,
                "project_name": project_name,
                "status_code": project_status,
                "official_progress_percent": (
                    project_official_progress
                ),
                "reference_progress_percent": (
                    project_reference_progress
                ),
                "progress_weight": project_weight,
                "is_included_in_progress": bool(
                    project_included
                    and excluded_on is None
                ),
                "excluded_on": excluded_on,
                "display_order": display_order,
                "stages": stages,
            }
        )

    return {
        "plan_id": db_plan_id,
        "plan_name": plan_name,
        "status_code": plan_status,
        "official_progress_percent": (
            plan_official_progress
        ),
        "reference_progress_percent": (
            plan_reference_progress
        ),
        "start_fiscal_year": start_fiscal_year,
        "end_fiscal_year": end_fiscal_year,
        "projects": projects,
    }
