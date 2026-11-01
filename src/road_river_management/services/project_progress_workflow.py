
"""Review and update a project's official progress."""

import sqlite3
from uuid import uuid4

from road_river_management.services.progress import (
    calculate_project_reference_progress,
    calculate_stage_progress,
)
from road_river_management.services.review_helpers import (
    upsert_plan_review_prompt,
)


def resolve_project_progress_review(
    connection: sqlite3.Connection,
    project_prompt_id: int,
    decision: str,
    *,
    new_official_progress_percent: float | None = None,
    resolution_reason: str | None = None,
    resolved_by_user_id: int | None = None,
) -> dict:
    """Resolve a project review and notify parent plans if updated."""

    allowed = {
        "REVIEWED_UPDATED",
        "REVIEWED_UNCHANGED",
        "DISMISSED",
    }
    if decision not in allowed:
        raise ValueError(f"Invalid review decision: {decision}")

    if decision == "REVIEWED_UPDATED":
        if new_official_progress_percent is None:
            raise ValueError("New official progress is required")
        new_percent = calculate_stage_progress(
            [(new_official_progress_percent, 1)]
        )
        assert new_percent is not None
    else:
        if new_official_progress_percent is not None:
            raise ValueError(
                "Progress value is only allowed when updating"
            )
        new_percent = None

    if connection.in_transaction:
        raise RuntimeError(
            "Commit or rollback the existing transaction first"
        )

    connection.execute("BEGIN IMMEDIATE")
    try:
        prompt = connection.execute(
            """
            SELECT project_id, review_status_code
            FROM project_progress_review_prompts
            WHERE project_prompt_id = ?
            """,
            (project_prompt_id,),
        ).fetchone()

        if prompt is None:
            raise ValueError(
                f"Project review prompt not found: {project_prompt_id}"
            )

        project_id, review_status = prompt
        if review_status != "PENDING":
            raise ValueError("Review prompt already resolved")

        project = connection.execute(
            """
            SELECT status_code, official_progress_percent
            FROM maintenance_projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        project_status, old_percent = project
        reason = (resolution_reason or "").strip()
        notified_plan_ids = []

        if decision == "REVIEWED_UPDATED":
            if new_percent == old_percent:
                raise ValueError("Official progress has not changed")

            connection.execute(
                """
                UPDATE maintenance_projects
                SET official_progress_percent = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE project_id = ?
                """,
                (new_percent, project_id),
            )

            connection.execute(
                """
                INSERT INTO project_progress_history (
                    project_id,
                    progress_scope,
                    previous_progress_percent,
                    new_progress_percent,
                    previous_status_code,
                    new_status_code,
                    change_reason,
                    changed_by_user_id
                )
                VALUES (?, 'PROJECT', ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    old_percent,
                    new_percent,
                    project_status,
                    project_status,
                    reason or None,
                    resolved_by_user_id,
                ),
            )

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

            operation_uuid = str(uuid4())

            for (plan_id,) in plan_rows:
                upsert_plan_review_prompt(
                    connection,
                    plan_id,
                    project_id,
                    None,
                    operation_uuid,
                    "PROJECT_PROGRESS_CHANGED",
                )
                notified_plan_ids.append(plan_id)

        connection.execute(
            """
            UPDATE project_progress_review_prompts
            SET review_status_code = ?,
                resolved_on = CURRENT_TIMESTAMP,
                resolved_by_user_id = ?,
                resolution_reason = ?
            WHERE project_prompt_id = ?
            """,
            (
                decision,
                resolved_by_user_id,
                reason or None,
                project_prompt_id,
            ),
        )

        connection.commit()

        return {
            "project_prompt_id": project_prompt_id,
            "project_id": project_id,
            "decision": decision,
            "previous_official_progress_percent": old_percent,
            "current_official_progress_percent": (
                new_percent if new_percent is not None else old_percent
            ),
            "notified_plan_ids": notified_plan_ids,
            "reference_progress_percent": (
                calculate_project_reference_progress(
                    connection, project_id
                )
            ),
        }

    except Exception:
        connection.rollback()
        raise
