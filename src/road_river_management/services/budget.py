
"""Fiscal budget request, assessment and appropriation services.

Requires SQLite migration 0011.

All write operations use BEGIN IMMEDIATE and commit atomically.
Callers must not already have an active transaction.

Amounts are integer Japanese yen. Fiscal years use the April-start
Japanese fiscal year convention but are stored as Gregorian years.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

ASSESSMENT_RESULTS = {
    "FULL",
    "REDUCED",
    "ZERO",
    "HELD",
    "REJECTED",
}

REQUEST_CATEGORIES = {
    "SURVEY",
    "DESIGN",
    "CONSTRUCTION",
    "LAND_ACQUISITION",
    "MAINTENANCE",
    "OTHER",
}

APPROPRIATION_TYPES = {
    "ORIGINAL",
    "SUPPLEMENTAL",
}


@contextmanager
def _transaction(connection: sqlite3.Connection):
    """Run one independent write transaction."""

    if connection.in_transaction:
        raise RuntimeError(
            "Commit or rollback the current transaction first"
        )

    connection.execute("BEGIN IMMEDIATE")

    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _amount(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer in yen")

    if value < 0:
        raise ValueError(f"{name} must be non-negative")

    return value


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value <= 0:
        raise ValueError(f"{name} must be positive")

    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")

    return value.strip()


def _date(value: str, name: str) -> str:
    """Validate an ISO-format date string (YYYY-MM-DD)."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string in YYYY-MM-DD format")

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a valid YYYY-MM-DD date"
        ) from exc

    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be YYYY-MM-DD")

    return value


def _fiscal_year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")

    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year is out of range")

    return value


def _get_request(
    connection: sqlite3.Connection,
    budget_request_id: int,
) -> tuple:
    row = connection.execute(
        """
        SELECT
            budget_request_id,
            project_id,
            stage_id,
            fiscal_year,
            request_status_code,
            current_requested_amount_yen,
            request_purpose
        FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (budget_request_id,),
    ).fetchone()

    if row is None:
        raise ValueError(
            f"Budget request not found: {budget_request_id}"
        )

    return row


def _event(
    connection: sqlite3.Connection,
    budget_request_id: int,
    event_type_code: str,
    *,
    previous_status_code: str | None = None,
    new_status_code: str | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO budget_request_events (
            budget_request_id,
            event_type_code,
            previous_status_code,
            new_status_code,
            event_description,
            acted_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            budget_request_id,
            event_type_code,
            previous_status_code,
            new_status_code,
            description,
            user_id,
        ),
    )


def create_budget_request(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    fiscal_year: int,
    request_title: str,
    requested_amount_yen: int,
    stage_id: int | None = None,
    request_category_code: str = "OTHER",
    request_purpose: str | None = None,
    urgency_reason: str | None = None,
    requested_by_department_id: int | None = None,
    requested_by_user_id: int | None = None,
    request_code: str | None = None,
) -> int:
    """Create a draft request and its initial workflow event."""

    _positive_id(project_id, "project_id")
    _fiscal_year(fiscal_year)
    title = _text(request_title, "request_title")
    amount = _amount(requested_amount_yen, "requested_amount_yen")

    if request_category_code not in REQUEST_CATEGORIES:
        raise ValueError("Invalid request category")

    with _transaction(connection):
        request_id = connection.execute(
            """
            INSERT INTO budget_requests (
                request_uuid,
                request_code,
                project_id,
                stage_id,
                fiscal_year,
                request_title,
                request_category_code,
                original_requested_amount_yen,
                current_requested_amount_yen,
                request_purpose,
                urgency_reason,
                requested_by_department_id,
                requested_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                request_code,
                project_id,
                stage_id,
                fiscal_year,
                title,
                request_category_code,
                amount,
                amount,
                request_purpose,
                urgency_reason,
                requested_by_department_id,
                requested_by_user_id,
            ),
        ).lastrowid

        _event(
            connection,
            request_id,
            "CREATED",
            new_status_code="DRAFT",
            description="Budget request created",
            user_id=requested_by_user_id,
        )

    return request_id


def revise_budget_request(
    connection: sqlite3.Connection,
    budget_request_id: int,
    *,
    new_requested_amount_yen: int,
    revision_reason: str,
    new_request_purpose: str | None = None,
    revised_by_user_id: int | None = None,
) -> int:
    """Change a request and record its revision atomically."""

    new_amount = _amount(
        new_requested_amount_yen,
        "new_requested_amount_yen",
    )
    reason = _text(revision_reason, "revision_reason")

    with _transaction(connection):
        (
            _,
            _,
            _,
            _,
            status,
            old_amount,
            old_purpose,
        ) = _get_request(connection, budget_request_id)

        if status not in {"DRAFT", "SUBMITTED", "UNDER_REVIEW"}:
            raise ValueError(
                f"Request cannot be revised in status: {status}"
            )

        purpose = (
            old_purpose
            if new_request_purpose is None
            else new_request_purpose
        )

        if new_amount == old_amount and purpose == old_purpose:
            raise ValueError("No request changes")

        revision_number = connection.execute(
            """
            SELECT COALESCE(MAX(revision_number), 0) + 1
            FROM budget_request_revisions
            WHERE budget_request_id = ?
            """,
            (budget_request_id,),
        ).fetchone()[0]

        connection.execute(
            """
            UPDATE budget_requests
            SET current_requested_amount_yen = ?,
                request_purpose = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE budget_request_id = ?
            """,
            (new_amount, purpose, budget_request_id),
        )

        connection.execute(
            """
            INSERT INTO budget_request_revisions (
                budget_request_id,
                revision_number,
                previous_requested_amount_yen,
                new_requested_amount_yen,
                previous_request_purpose,
                new_request_purpose,
                revision_reason,
                revised_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                budget_request_id,
                revision_number,
                old_amount,
                new_amount,
                old_purpose,
                purpose,
                reason,
                revised_by_user_id,
            ),
        )

        _event(
            connection,
            budget_request_id,
            "REVISED",
            previous_status_code=status,
            new_status_code=status,
            description=reason,
            user_id=revised_by_user_id,
        )

    return revision_number


def submit_budget_request(
    connection: sqlite3.Connection,
    budget_request_id: int,
    *,
    submitted_on: str,
    submitted_by_user_id: int | None = None,
) -> None:
    """Submit an existing draft request."""

    submitted_date = _date(submitted_on, "submitted_on")

    with _transaction(connection):
        row = _get_request(connection, budget_request_id)
        old_status = row[4]

        if old_status != "DRAFT":
            raise ValueError("Only draft requests can be submitted")

        connection.execute(
            """
            UPDATE budget_requests
            SET request_status_code = 'SUBMITTED',
                submitted_on = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE budget_request_id = ?
            """,
            (submitted_date, budget_request_id),
        )

        _event(
            connection,
            budget_request_id,
            "SUBMITTED",
            previous_status_code=old_status,
            new_status_code="SUBMITTED",
            description=f"Submitted on {submitted_date}",
            user_id=submitted_by_user_id,
        )


def record_budget_assessment(
    connection: sqlite3.Connection,
    budget_request_id: int,
    *,
    assessment_result_code: str,
    assessed_amount_yen: int | None,
    assessed_on: str,
    assessment_reason: str | None = None,
    conditions_text: str | None = None,
    assessed_by_department_id: int | None = None,
    assessed_by_user_id: int | None = None,
) -> int:
    """Record the next assessment round without changing request sums."""

    if assessment_result_code not in ASSESSMENT_RESULTS:
        raise ValueError("Invalid assessment result")

    assessed_date = _date(assessed_on, "assessed_on")

    if assessed_amount_yen is not None:
        _amount(assessed_amount_yen, "assessed_amount_yen")

    with _transaction(connection):
        row = _get_request(connection, budget_request_id)
        old_status = row[4]
        requested_amount = row[5]

        if old_status not in {
            "SUBMITTED",
            "UNDER_REVIEW",
            "ASSESSED",
            "ON_HOLD",
        }:
            raise ValueError(
                f"Request cannot be assessed in status: {old_status}"
            )

        if assessment_result_code == "FULL":
            valid = assessed_amount_yen == requested_amount
        elif assessment_result_code == "REDUCED":
            valid = (
                assessed_amount_yen is not None
                and 0 < assessed_amount_yen < requested_amount
            )
        elif assessment_result_code == "ZERO":
            valid = assessed_amount_yen == 0
        elif assessment_result_code == "HELD":
            valid = assessed_amount_yen is None
        else:
            valid = assessed_amount_yen is None or assessed_amount_yen == 0

        if not valid:
            raise ValueError(
                "Assessed amount does not match assessment result"
            )

        round_number = connection.execute(
            """
            SELECT COALESCE(MAX(assessment_round), 0) + 1
            FROM budget_assessments
            WHERE budget_request_id = ?
            """,
            (budget_request_id,),
        ).fetchone()[0]

        connection.execute(
            """
            INSERT INTO budget_assessments (
                budget_request_id,
                assessment_round,
                assessment_result_code,
                requested_amount_at_assessment_yen,
                assessed_amount_yen,
                assessment_reason,
                conditions_text,
                assessed_by_department_id,
                assessed_by_user_id,
                assessed_on
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                budget_request_id,
                round_number,
                assessment_result_code,
                requested_amount,
                assessed_amount_yen,
                assessment_reason,
                conditions_text,
                assessed_by_department_id,
                assessed_by_user_id,
                assessed_date,
            ),
        )

        new_status = (
            "ON_HOLD"
            if assessment_result_code == "HELD"
            else "ASSESSED"
        )

        connection.execute(
            """
            UPDATE budget_requests
            SET request_status_code = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE budget_request_id = ?
            """,
            (new_status, budget_request_id),
        )

        _event(
            connection,
            budget_request_id,
            "HELD"
            if assessment_result_code == "HELD"
            else "ASSESSMENT_RECORDED",
            previous_status_code=old_status,
            new_status_code=new_status,
            description=assessment_reason,
            user_id=assessed_by_user_id,
        )

    return round_number


def record_budget_appropriation(
    connection: sqlite3.Connection,
    budget_request_id: int,
    *,
    appropriation_type_code: str,
    appropriated_amount_yen: int,
    approved_on: str,
    appropriation_number: int = 1,
    effective_on: str | None = None,
    decision_reference: str | None = None,
    decision_notes: str | None = None,
    recorded_by_user_id: int | None = None,
) -> int:
    """Record a formal appropriation separately from assessments."""

    if appropriation_type_code not in APPROPRIATION_TYPES:
        raise ValueError("Invalid appropriation type")

    amount = _amount(
        appropriated_amount_yen,
        "appropriated_amount_yen",
    )
    number = _positive_id(
        appropriation_number,
        "appropriation_number",
    )
    approval_date = _date(approved_on, "approved_on")

    if effective_on is not None:
        _date(effective_on, "effective_on")

    with _transaction(connection):
        row = _get_request(connection, budget_request_id)

        if row[4] != "ASSESSED":
            raise ValueError(
                "Request must have a completed assessment"
            )

        latest = connection.execute(
            """
            SELECT assessment_result_code
            FROM budget_assessments
            WHERE budget_request_id = ?
            ORDER BY assessment_round DESC
            LIMIT 1
            """,
            (budget_request_id,),
        ).fetchone()

        if latest is None or latest[0] in {"HELD", "REJECTED"}:
            raise ValueError(
                "Latest assessment does not permit appropriation"
            )

        appropriation_id = connection.execute(
            """
            INSERT INTO budget_appropriations (
                budget_request_id,
                appropriation_type_code,
                appropriation_number,
                appropriated_amount_yen,
                approved_on,
                effective_on,
                decision_reference,
                decision_notes,
                recorded_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                budget_request_id,
                appropriation_type_code,
                number,
                amount,
                approval_date,
                effective_on,
                decision_reference,
                decision_notes,
                recorded_by_user_id,
            ),
        ).lastrowid

        _event(
            connection,
            budget_request_id,
            "COMMENT",
            previous_status_code=row[4],
            new_status_code=row[4],
            description=(
                f"Appropriation recorded: "
                f"{appropriation_type_code} #{number}, "
                f"{amount} JPY"
            ),
            user_id=recorded_by_user_id,
        )

    return appropriation_id


def create_followup_budget_request(
    connection: sqlite3.Connection,
    previous_request_id: int,
    *,
    fiscal_year: int,
    requested_amount_yen: int,
    request_title: str | None = None,
    requested_by_user_id: int | None = None,
) -> int:
    """Create a new fiscal-year request linked to an earlier request."""

    new_year = _fiscal_year(fiscal_year)
    amount = _amount(requested_amount_yen, "requested_amount_yen")

    with _transaction(connection):
        previous = connection.execute(
            """
            SELECT project_id, stage_id, fiscal_year,
                   request_title, request_category_code,
                   request_purpose, urgency_reason
            FROM budget_requests
            WHERE budget_request_id = ?
            """,
            (previous_request_id,),
        ).fetchone()

        if previous is None:
            raise ValueError(
                f"Previous request not found: {previous_request_id}"
            )

        (
            project_id,
            stage_id,
            previous_year,
            previous_title,
            category,
            purpose,
            urgency,
        ) = previous

        if new_year <= previous_year:
            raise ValueError(
                "Follow-up fiscal year must be later"
            )

        title = (
            previous_title
            if request_title is None
            else _text(request_title, "request_title")
        )

        new_id = connection.execute(
            """
            INSERT INTO budget_requests (
                request_uuid,
                project_id,
                stage_id,
                fiscal_year,
                request_title,
                request_category_code,
                original_requested_amount_yen,
                current_requested_amount_yen,
                previous_request_id,
                request_purpose,
                urgency_reason,
                requested_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                project_id,
                stage_id,
                new_year,
                title,
                category,
                amount,
                amount,
                previous_request_id,
                purpose,
                urgency,
                requested_by_user_id,
            ),
        ).lastrowid

        _event(
            connection,
            new_id,
            "CREATED",
            new_status_code="DRAFT",
            description=(
                f"Follow-up of budget request {previous_request_id}"
            ),
            user_id=requested_by_user_id,
        )

    return new_id


def get_project_budget_summary(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    fiscal_year: int | None = None,
) -> dict:
    """Return one row per request without duplicating assessment sums."""

    _positive_id(project_id, "project_id")

    if fiscal_year is not None:
        _fiscal_year(fiscal_year)

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

    rows = connection.execute(
        """
        SELECT
            r.budget_request_id,
            r.fiscal_year,
            r.request_title,
            r.request_status_code,
            r.previous_request_id,
            r.original_requested_amount_yen,
            r.current_requested_amount_yen,
            (
                SELECT a.assessment_result_code
                FROM budget_assessments AS a
                WHERE a.budget_request_id = r.budget_request_id
                ORDER BY a.assessment_round DESC
                LIMIT 1
            ) AS latest_assessment_result,
            (
                SELECT a.assessed_amount_yen
                FROM budget_assessments AS a
                WHERE a.budget_request_id = r.budget_request_id
                ORDER BY a.assessment_round DESC
                LIMIT 1
            ) AS latest_assessed_amount_yen,
            (
                SELECT COALESCE(
                    SUM(p.appropriated_amount_yen), 0
                )
                FROM budget_appropriations AS p
                WHERE p.budget_request_id = r.budget_request_id
            ) AS total_appropriated_amount_yen
        FROM budget_requests AS r
        WHERE r.project_id = ?
          AND (? IS NULL OR r.fiscal_year = ?)
        ORDER BY r.fiscal_year, r.budget_request_id
        """,
        (project_id, fiscal_year, fiscal_year),
    ).fetchall()

    requests = []

    for row in rows:
        requests.append(
            {
                "budget_request_id": row[0],
                "fiscal_year": row[1],
                "request_title": row[2],
                "request_status_code": row[3],
                "previous_request_id": row[4],
                "original_requested_amount_yen": row[5],
                "current_requested_amount_yen": row[6],
                "latest_assessment_result": row[7],
                "latest_assessed_amount_yen": row[8],
                "total_appropriated_amount_yen": row[9],
            }
        )

    return {
        "project_id": project_id,
        "fiscal_year": fiscal_year,
        "request_count": len(requests),
        "total_current_requested_amount_yen": sum(
            row["current_requested_amount_yen"]
            for row in requests
        ),
        "total_appropriated_amount_yen": sum(
            row["total_appropriated_amount_yen"]
            for row in requests
        ),
        "requests": requests,
    }
