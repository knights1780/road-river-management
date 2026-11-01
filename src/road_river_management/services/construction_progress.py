
"""Construction progress and hierarchical reference progress.

Reference progress uses the canonical progress_reference service.

Construction work progress and status changes are recorded in history.
Official stage, project and plan progress is never automatically updated.

Requires SQLite migration 0010.
"""

import math
import sqlite3
from uuid import uuid4

from road_river_management.services.construction_transitions import (
    validate_construction_transition,
)
from road_river_management.services.progress_reference import (
    calculate_plan_reference_progress as _canonical_plan_progress,
)
from road_river_management.services.progress_reference import (
    calculate_project_reference_progress as _canonical_project_progress,
)
from road_river_management.services.progress_reference import (
    calculate_stage_reference_progress as _canonical_stage_progress,
)

REVIEW_THRESHOLD_POINTS = 10.0


def _percent(value: float) -> float:
    """Validate a percentage between 0 and 100."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Progress must be numeric")

    result = float(value)

    if not math.isfinite(result) or not 0 <= result <= 100:
        raise ValueError("Progress must be between 0 and 100")

    return result


def calculate_construction_stage_reference_progress(
    connection: sqlite3.Connection,
    stage_id: int,
) -> float | None:
    """Calculate stage reference using the canonical service."""

    return _canonical_stage_progress(connection, stage_id)


def calculate_construction_project_reference_progress(
    connection: sqlite3.Connection,
    project_id: int,
) -> float | None:
    """Calculate project reference using the canonical service."""

    return _canonical_project_progress(connection, project_id)


def calculate_construction_plan_reference_progress(
    connection: sqlite3.Connection,
    plan_id: int,
) -> float | None:
    """Calculate plan reference using the canonical service."""

    return _canonical_plan_progress(connection, plan_id)


def _project_prompt(
    connection: sqlite3.Connection,
    project_id: int,
    stage_id: int,
    operation_uuid: str,
) -> None:
    """Create or consolidate a project progress review prompt."""

    reference = calculate_construction_project_reference_progress(
        connection,
        project_id,
    )

    project = connection.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()

    if project is None:
        raise ValueError(f"Project not found: {project_id}")

    official = project[0]

    pending = connection.execute(
        """
        SELECT project_prompt_id
        FROM project_progress_review_prompts
        WHERE project_id = ?
          AND review_status_code = 'PENDING'
        """,
        (project_id,),
    ).fetchone()

    if pending is not None:
        connection.execute(
            """
            UPDATE project_progress_review_prompts
            SET
                latest_stage_id = ?,
                latest_operation_uuid = ?,
                prompt_reason_code = 'STAGE_PROGRESS_CHANGED',
                reference_progress_percent = ?,
                official_progress_percent = ?,
                event_count = event_count + 1,
                last_notified_at = CURRENT_TIMESTAMP
            WHERE project_prompt_id = ?
            """,
            (
                stage_id,
                operation_uuid,
                reference,
                official,
                pending[0],
            ),
        )
        return

    connection.execute(
        """
        INSERT INTO project_progress_review_prompts (
            prompt_uuid,
            project_id,
            latest_stage_id,
            first_operation_uuid,
            latest_operation_uuid,
            prompt_reason_code,
            reference_progress_percent,
            official_progress_percent
        )
        VALUES (?, ?, ?, ?, ?, 'STAGE_PROGRESS_CHANGED', ?, ?)
        """,
        (
            str(uuid4()),
            project_id,
            stage_id,
            operation_uuid,
            operation_uuid,
            reference,
            official,
        ),
    )


def _plan_prompt(
    connection: sqlite3.Connection,
    plan_id: int,
    project_id: int,
    stage_id: int,
    operation_uuid: str,
) -> None:
    """Create or consolidate a plan progress review prompt."""

    reference = calculate_construction_plan_reference_progress(
        connection,
        plan_id,
    )

    plan = connection.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()

    if plan is None:
        raise ValueError(f"Plan not found: {plan_id}")

    official = plan[0]

    pending = connection.execute(
        """
        SELECT prompt_id
        FROM progress_review_prompts
        WHERE plan_id = ?
          AND review_status_code = 'PENDING'
        ORDER BY prompt_id
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()

    if pending is not None:
        connection.execute(
            """
            UPDATE progress_review_prompts
            SET
                project_id = ?,
                stage_id = ?,
                prompt_reason_code = 'STAGE_PROGRESS_CHANGED',
                reference_progress_percent = ?,
                official_progress_percent = ?
            WHERE prompt_id = ?
            """,
            (
                project_id,
                stage_id,
                reference,
                official,
                pending[0],
            ),
        )
        return

    connection.execute(
        """
        INSERT INTO progress_review_prompts (
            prompt_uuid,
            operation_uuid,
            plan_id,
            project_id,
            stage_id,
            prompt_reason_code,
            reference_progress_percent,
            official_progress_percent
        )
        VALUES (?, ?, ?, ?, ?, 'STAGE_PROGRESS_CHANGED', ?, ?)
        """,
        (
            str(uuid4()),
            operation_uuid,
            plan_id,
            project_id,
            stage_id,
            reference,
            official,
        ),
    )


def update_construction_progress(
    connection: sqlite3.Connection,
    construction_work_id: int,
    new_progress_percent: float,
    *,
    new_status_code: str | None = None,
    change_reason: str | None = None,
    changed_by_user_id: int | None = None,
    confirmed: bool = False,
) -> dict:
    """Update construction progress, history and affected review prompts.

    Operations are executed in one transaction.

    The connection must not already have an active transaction.

    Official stage, project and plan progress values are not changed.
    """

    new_percent = _percent(new_progress_percent)

    if connection.in_transaction:
        raise RuntimeError(
            "Commit or rollback the current transaction first"
        )

    connection.execute("BEGIN IMMEDIATE")

    try:
        work = connection.execute(
            """
            SELECT
                status_code,
                official_progress_percent
            FROM construction_works
            WHERE construction_work_id = ?
            """,
            (construction_work_id,),
        ).fetchone()

        if work is None:
            raise ValueError(
                f"Construction work not found: {construction_work_id}"
            )

        old_status, old_percent = work

        new_status = (
            old_status
            if new_status_code is None
            else new_status_code
        )

        reason = (change_reason or "").strip()

        if new_status != old_status:
            valid_status = connection.execute(
                """
                SELECT 1
                FROM construction_statuses
                WHERE status_code = ?
                  AND is_active = 1
                """,
                (new_status,),
            ).fetchone()

            if valid_status is None:
                raise ValueError(
                    f"Invalid construction status: {new_status}"
                )

            validate_construction_transition(
                old_status,
                new_status,
                change_reason=reason,
                confirmed=confirmed,
            )

        if new_percent == old_percent and new_status == old_status:
            raise ValueError("No progress or status change")

        connection.execute(
            """
            UPDATE construction_works
            SET
                official_progress_percent = ?,
                status_code = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE construction_work_id = ?
            """,
            (
                new_percent,
                new_status,
                construction_work_id,
            ),
        )

        connection.execute(
            """
            INSERT INTO construction_status_history (
                construction_work_id,
                previous_status_code,
                new_status_code,
                previous_progress_percent,
                new_progress_percent,
                change_reason,
                changed_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                construction_work_id,
                old_status,
                new_status,
                old_percent,
                new_percent,
                reason or None,
                changed_by_user_id,
            ),
        )

        progress_delta = abs(new_percent - old_percent)

        requires_review = (
            progress_delta >= REVIEW_THRESHOLD_POINTS
            or new_status != old_status
        )

        operation_uuid = str(uuid4())
        notified_project_ids = []
        notified_plan_ids = []

        if requires_review:
            affected_stages = connection.execute(
                """
                SELECT DISTINCT
                    project_id,
                    stage_id
                FROM project_construction_links
                WHERE construction_work_id = ?
                ORDER BY project_id, stage_id
                """,
                (construction_work_id,),
            ).fetchall()

            notified_projects = set()
            notified_plans = set()

            for project_id, stage_id in affected_stages:
                if project_id not in notified_projects:
                    _project_prompt(
                        connection,
                        project_id,
                        stage_id,
                        operation_uuid,
                    )

                    notified_projects.add(project_id)
                    notified_project_ids.append(project_id)

                plan_rows = connection.execute(
                    """
                    SELECT DISTINCT plan_id
                    FROM maintenance_plan_items
                    WHERE project_id = ?
                      AND is_included_in_progress = 1
                      AND excluded_on IS NULL
                    ORDER BY plan_id
                    """,
                    (project_id,),
                ).fetchall()

                for (plan_id,) in plan_rows:
                    if plan_id in notified_plans:
                        continue

                    _plan_prompt(
                        connection,
                        plan_id,
                        project_id,
                        stage_id,
                        operation_uuid,
                    )

                    notified_plans.add(plan_id)
                    notified_plan_ids.append(plan_id)

        connection.commit()

        return {
            "construction_work_id": construction_work_id,
            "previous_progress_percent": old_percent,
            "new_progress_percent": new_percent,
            "previous_status_code": old_status,
            "new_status_code": new_status,
            "operation_uuid": operation_uuid,
            "notified_project_ids": notified_project_ids,
            "notified_plan_ids": notified_plan_ids,
        }

    except Exception:
        connection.rollback()
        raise
