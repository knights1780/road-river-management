
"""Transactional progress updates and plan review reminders."""

import sqlite3
from uuid import uuid4

from road_river_management.services.progress import (
    calculate_plan_reference_progress,
    calculate_stage_progress,
)
from road_river_management.services.review_helpers import (
    upsert_plan_review_prompt,
    upsert_project_review_prompt,
)

REVIEW_THRESHOLD_POINTS = 10.0


def _valid_percent(value: float) -> float:
    """Validate a progress percentage."""
    result = calculate_stage_progress([(value, 1.0)])
    assert result is not None
    return result


def _require_no_transaction(
    connection: sqlite3.Connection,
) -> None:
    """Ensure the caller has no active transaction."""
    if connection.in_transaction:
        raise RuntimeError(
            "Commit or rollback the existing transaction first"
        )


def update_stage_progress(
    connection: sqlite3.Connection,
    stage_id: int,
    new_progress_percent: float,
    *,
    new_status_code: str | None = None,
    change_reason: str | None = None,
    changed_by_user_id: int | None = None,
    confirmed: bool = False,
) -> dict:
    """Update a stage and atomically record history and review prompts.

    Official project and plan progress values are never
    automatically modified.

    The connection must not already be in a transaction.
    """

    new_percent = _valid_percent(new_progress_percent)
    _require_no_transaction(connection)

    connection.execute("BEGIN IMMEDIATE")

    try:
        stage = connection.execute(
            """
            SELECT
                project_id,
                status_code,
                progress_percent
            FROM project_stages
            WHERE stage_id = ?
            """,
            (stage_id,),
        ).fetchone()

        if stage is None:
            raise ValueError(f"Stage not found: {stage_id}")

        project_id, old_status, old_percent = stage

        new_status = (
            old_status
            if new_status_code is None
            else new_status_code
        )

        status_changed = new_status != old_status
        progress_changed = new_percent != old_percent

        if not status_changed and not progress_changed:
            raise ValueError("No progress or status change")

        reason = (change_reason or "").strip()
        transition_requires_review = False

        if status_changed:
            transition = connection.execute(
                """
                SELECT
                    requires_reason,
                    requires_confirmation,
                    requires_progress_review
                FROM workflow_transitions
                WHERE workflow_type = 'STAGE'
                  AND from_status_code = ?
                  AND to_status_code = ?
                  AND is_active = 1
                """,
                (old_status, new_status),
            ).fetchone()

            if transition is None:
                raise ValueError(
                    "Invalid stage transition: "
                    f"{old_status} -> {new_status}"
                )

            (
                requires_reason,
                requires_confirmation,
                transition_review_flag,
            ) = transition

            if requires_reason and not reason:
                raise ValueError(
                    "This status change requires a reason"
                )

            if requires_confirmation and not confirmed:
                raise ValueError(
                    "This status change requires confirmation"
                )

            transition_requires_review = bool(
                transition_review_flag
            )

        connection.execute(
            """
            UPDATE project_stages
            SET
                status_code = ?,
                progress_percent = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE stage_id = ?
            """,
            (
                new_status,
                new_percent,
                stage_id,
            ),
        )

        connection.execute(
            """
            INSERT INTO project_progress_history (
                project_id,
                stage_id,
                progress_scope,
                previous_progress_percent,
                new_progress_percent,
                previous_status_code,
                new_status_code,
                change_reason,
                changed_by_user_id
            )
            VALUES (?, ?, 'STAGE', ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                stage_id,
                old_percent,
                new_percent,
                old_status,
                new_status,
                reason or None,
                changed_by_user_id,
            ),
        )

        # Determine whether this change requires a review.
        progress_delta = abs(new_percent - old_percent)

        requires_review = (
            transition_requires_review
            or progress_delta >= REVIEW_THRESHOLD_POINTS
        )

        operation_uuid = str(uuid4())
        notified_plan_ids = []

        if requires_review:
            reason_code = (
                "STAGE_STATUS_CHANGED"
                if status_changed
                else "STAGE_PROGRESS_CHANGED"
            )

            # Create or consolidate the project-level prompt.
            upsert_project_review_prompt(
                connection,
                project_id,
                stage_id,
                operation_uuid,
                reason_code,
            )

            # Find every active plan containing this project.
            plans = connection.execute(
                """
                SELECT DISTINCT item.plan_id
                FROM maintenance_plan_items AS item
                WHERE item.project_id = ?
                  AND item.is_included_in_progress = 1
                  AND item.excluded_on IS NULL
                ORDER BY item.plan_id
                """,
                (project_id,),
            ).fetchall()

            # Create or consolidate a prompt for each plan.
            for (plan_id,) in plans:
                upsert_plan_review_prompt(
                    connection,
                    plan_id,
                    project_id,
                    stage_id,
                    operation_uuid,
                    reason_code,
                )
                notified_plan_ids.append(plan_id)

        connection.commit()

        return {
            "stage_id": stage_id,
            "project_id": project_id,
            "previous_progress_percent": old_percent,
            "new_progress_percent": new_percent,
            "previous_status_code": old_status,
            "new_status_code": new_status,
            "operation_uuid": operation_uuid,
            "notified_plan_ids": notified_plan_ids,
        }

    except Exception:
        connection.rollback()
        raise


def resolve_plan_progress_review(
    connection: sqlite3.Connection,
    prompt_id: int,
    decision: str,
    *,
    new_official_progress_percent: float | None = None,
    resolution_reason: str | None = None,
    resolved_by_user_id: int | None = None,
) -> dict:
    """Resolve a plan review prompt.

    Valid decisions:
        REVIEWED_UPDATED
        REVIEWED_UNCHANGED
        DISMISSED

    The official progress is changed only when the
    decision is REVIEWED_UPDATED.
    """

    allowed_decisions = {
        "REVIEWED_UPDATED",
        "REVIEWED_UNCHANGED",
        "DISMISSED",
    }

    if decision not in allowed_decisions:
        raise ValueError(
            f"Invalid review decision: {decision}"
        )

    if decision == "REVIEWED_UPDATED":
        if new_official_progress_percent is None:
            raise ValueError(
                "New official progress is required"
            )

        new_percent = _valid_percent(
            new_official_progress_percent
        )
    else:
        if new_official_progress_percent is not None:
            raise ValueError(
                "Official progress can only be set when updating"
            )

        new_percent = None

    _require_no_transaction(connection)
    connection.execute("BEGIN IMMEDIATE")

    try:
        prompt = connection.execute(
            """
            SELECT
                plan_id,
                review_status_code
            FROM progress_review_prompts
            WHERE prompt_id = ?
            """,
            (prompt_id,),
        ).fetchone()

        if prompt is None:
            raise ValueError(
                f"Review prompt not found: {prompt_id}"
            )

        plan_id, review_status = prompt

        if review_status != "PENDING":
            raise ValueError(
                "Review prompt already resolved"
            )

        plan = connection.execute(
            """
            SELECT
                status_code,
                official_progress_percent
            FROM maintenance_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()

        if plan is None:
            raise ValueError(
                f"Plan not found: {plan_id}"
            )

        plan_status, old_percent = plan
        reason = (resolution_reason or "").strip()

        if decision == "REVIEWED_UPDATED":
            if new_percent == old_percent:
                raise ValueError(
                    "Official progress has not changed"
                )

            reference_progress = (
                calculate_plan_reference_progress(
                    connection,
                    plan_id,
                )
            )

            connection.execute(
                """
                UPDATE maintenance_plans
                SET
                    official_progress_percent = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE plan_id = ?
                """,
                (
                    new_percent,
                    plan_id,
                ),
            )

            connection.execute(
                """
                INSERT INTO plan_progress_history (
                    plan_id,
                    previous_progress_percent,
                    new_progress_percent,
                    previous_status_code,
                    new_status_code,
                    reference_progress_percent,
                    change_reason,
                    changed_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    old_percent,
                    new_percent,
                    plan_status,
                    plan_status,
                    reference_progress,
                    reason or None,
                    resolved_by_user_id,
                ),
            )

        connection.execute(
            """
            UPDATE progress_review_prompts
            SET
                review_status_code = ?,
                resolved_on = CURRENT_TIMESTAMP,
                resolved_by_user_id = ?,
                resolution_reason = ?
            WHERE prompt_id = ?
            """,
            (
                decision,
                resolved_by_user_id,
                reason or None,
                prompt_id,
            ),
        )

        connection.commit()

        return {
            "prompt_id": prompt_id,
            "plan_id": plan_id,
            "decision": decision,
            "previous_official_progress_percent": old_percent,
            "current_official_progress_percent": (
                new_percent
                if new_percent is not None
                else old_percent
            ),
        }

    except Exception:
        connection.rollback()
        raise
