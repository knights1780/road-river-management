
"""Create or consolidate progress-review reminders.

Caller owns the database transaction.
"""

import sqlite3
from uuid import uuid4

from road_river_management.services.progress import (
    calculate_plan_reference_progress,
    calculate_project_reference_progress,
)


def upsert_project_review_prompt(
    connection: sqlite3.Connection,
    project_id: int,
    stage_id: int,
    operation_uuid: str,
    reason_code: str,
) -> int:
    """Create or refresh the project's pending reminder."""

    reference = calculate_project_reference_progress(
        connection, project_id
    )

    row = connection.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Project not found: {project_id}")

    official = row[0]

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
        prompt_id = pending[0]
        connection.execute(
            """
            UPDATE project_progress_review_prompts
            SET latest_stage_id = ?,
                latest_operation_uuid = ?,
                prompt_reason_code = ?,
                reference_progress_percent = ?,
                official_progress_percent = ?,
                event_count = event_count + 1,
                last_notified_at = CURRENT_TIMESTAMP
            WHERE project_prompt_id = ?
            """,
            (
                stage_id,
                operation_uuid,
                reason_code,
                reference,
                official,
                prompt_id,
            ),
        )
        return prompt_id

    cursor = connection.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            project_id,
            stage_id,
            operation_uuid,
            operation_uuid,
            reason_code,
            reference,
            official,
        ),
    )
    return cursor.lastrowid


def upsert_plan_review_prompt(
    connection: sqlite3.Connection,
    plan_id: int,
    project_id: int,
    stage_id: int | None,
    operation_uuid: str,
    reason_code: str,
) -> int:
    """Create or consolidate an existing plan reminder."""

    reference = calculate_plan_reference_progress(
        connection, plan_id
    )

    row = connection.execute(
        """
        SELECT official_progress_percent
        FROM maintenance_plans
        WHERE plan_id = ?
        """,
        (plan_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Plan not found: {plan_id}")

    official = row[0]

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
        prompt_id = pending[0]
        connection.execute(
            """
            UPDATE progress_review_prompts
            SET project_id = ?,
                stage_id = ?,
                prompt_reason_code = ?,
                reference_progress_percent = ?,
                official_progress_percent = ?
            WHERE prompt_id = ?
            """,
            (
                project_id,
                stage_id,
                reason_code,
                reference,
                official,
                prompt_id,
            ),
        )
        return prompt_id

    cursor = connection.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            operation_uuid,
            plan_id,
            project_id,
            stage_id,
            reason_code,
            reference,
            official,
        ),
    )
    return cursor.lastrowid
