
"""Debt obligation and continuing expenditure services.

Requires SQLite migrations 0012 and 0013.

Amounts are integer yen. All mutations use BEGIN IMMEDIATE.
Authorization and amendment record administrative decisions;
they do not independently establish legal validity.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from uuid import uuid4

_SPECS = {
    "OBLIGATION": {
        "table": "budget_obligations",
        "id": "obligation_id",
        "uuid": "obligation_uuid",
        "title": "obligation_title",
        "amount": "limit_amount_yen",
        "status": "authorization_status_code",
        "year_table": "budget_obligation_years",
        "year_id": "obligation_id",
        "year_amount": "planned_payment_amount_yen",
    },
    "CONTINUING": {
        "table": "budget_continuing_expenditures",
        "id": "continuing_id",
        "uuid": "continuing_uuid",
        "title": "continuing_title",
        "amount": "total_amount_yen",
        "status": "status_code",
        "year_table": "budget_continuing_years",
        "year_id": "continuing_id",
        "year_amount": "annual_amount_yen",
    },
}


@contextmanager
def _transaction(connection: sqlite3.Connection):
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


def _integer(value: int, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _year(value: int) -> int:
    value = _integer(value, "fiscal_year")
    if not 1900 <= value <= 2200:
        raise ValueError("Fiscal year out of range")
    return value


def _required(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _date(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Date must be a string")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid date") from exc
    if result.isoformat() != value:
        raise ValueError("Date must be YYYY-MM-DD")
    return value


def _spec(kind: str) -> dict:
    if kind not in _SPECS:
        raise ValueError(f"Invalid multiyear budget type: {kind}")
    return _SPECS[kind]


def _snapshot(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
) -> dict:
    spec = _spec(kind)

    cursor = connection.execute(
        f"SELECT * FROM {spec['table']} "
        f"WHERE {spec['id']} = ?",
        (record_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"{kind} record not found: {record_id}")

    record = dict(zip(
        (column[0] for column in cursor.description),
        row,
        strict=True,
    ))

    year_rows = connection.execute(
        f"SELECT fiscal_year, {spec['year_amount']} "
        f"FROM {spec['year_table']} "
        f"WHERE {spec['year_id']} = ? "
        "ORDER BY fiscal_year",
        (record_id,),
    ).fetchall()

    return {
        "record": record,
        "years": [
            {
                "fiscal_year": year,
                "amount_yen": amount,
            }
            for year, amount in year_rows
        ],
    }


def _audit(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
    action: str,
    previous: dict | None,
    reason: str,
    decision_reference: str | None = None,
    user_id: int | None = None,
) -> None:
    current = _snapshot(connection, kind, record_id)

    connection.execute(
        """
        INSERT INTO budget_multiyear_audit (
            audit_uuid,
            record_type_code,
            record_id,
            action_code,
            previous_snapshot_json,
            new_snapshot_json,
            change_reason,
            decision_reference,
            changed_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            kind,
            record_id,
            action,
            (
                None
                if previous is None
                else json.dumps(
                    previous, ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            json.dumps(
                current, ensure_ascii=False,
                sort_keys=True,
            ),
            reason,
            decision_reference,
            user_id,
        ),
    )


def _validate_years(
    schedule: dict[int, int],
    start_year: int,
    end_year: int,
) -> None:
    if not isinstance(schedule, dict):
        raise TypeError("schedule must be a dictionary")

    for year, amount in schedule.items():
        _year(year)
        _integer(amount, "annual amount")
        if not start_year <= year <= end_year:
            raise ValueError(
                "Schedule year outside authorized period"
            )


def _validate_total(
    kind: str,
    total_amount: int,
    schedule: dict[int, int],
    *,
    require_complete: bool,
) -> None:
    scheduled = sum(schedule.values())

    if kind == "OBLIGATION":
        if scheduled > total_amount:
            raise ValueError(
                "Scheduled payments exceed obligation limit"
            )
    elif require_complete and scheduled != total_amount:
        raise ValueError(
            "Continuing annual amounts must equal total amount"
        )
    elif scheduled > total_amount:
        raise ValueError(
            "Continuing annual amounts exceed total amount"
        )


def _replace_years(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
    schedule: dict[int, int],
) -> None:
    spec = _spec(kind)

    connection.execute(
        f"DELETE FROM {spec['year_table']} "
        f"WHERE {spec['year_id']} = ?",
        (record_id,),
    )

    for year, amount in sorted(schedule.items()):
        connection.execute(
            f"INSERT INTO {spec['year_table']} "
            f"({spec['year_id']}, fiscal_year, "
            f"{spec['year_amount']}) VALUES (?, ?, ?)",
            (record_id, year, amount),
        )


def _create(
    connection: sqlite3.Connection,
    kind: str,
    *,
    project_id: int,
    title: str,
    start_fiscal_year: int,
    end_fiscal_year: int,
    amount_yen: int,
    authorization_fiscal_year: int | None = None,
    purpose: str | None = None,
    user_id: int | None = None,
) -> int:

    _integer(project_id, "project_id", 1)
    title = _required(title, "title")
    start = _year(start_fiscal_year)
    end = _year(end_fiscal_year)
    amount = _integer(amount_yen, "amount_yen")

    if start > end:
        raise ValueError("Start year must not exceed end year")

    if kind == "OBLIGATION":
        if authorization_fiscal_year is None:
            raise ValueError(
                "Authorization fiscal year is required"
            )
        authorization_fiscal_year = _year(
            authorization_fiscal_year
        )

    with _transaction(connection):
        project = connection.execute(
            """
            SELECT 1
            FROM maintenance_projects
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()

        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        if kind == "OBLIGATION":
            result = connection.execute(
                """
                INSERT INTO budget_obligations (
                    obligation_uuid,
                    project_id,
                    obligation_title,
                    authorization_fiscal_year,
                    start_fiscal_year,
                    end_fiscal_year,
                    limit_amount_yen,
                    purpose
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    project_id,
                    title,
                    authorization_fiscal_year,
                    start,
                    end,
                    amount,
                    purpose,
                ),
            )
        else:
            result = connection.execute(
                """
                INSERT INTO budget_continuing_expenditures (
                    continuing_uuid,
                    project_id,
                    continuing_title,
                    start_fiscal_year,
                    end_fiscal_year,
                    total_amount_yen,
                    purpose
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    project_id,
                    title,
                    start,
                    end,
                    amount,
                    purpose,
                ),
            )

        record_id = result.lastrowid

        _audit(
            connection,
            kind,
            record_id,
            "CREATED",
            None,
            "Draft created",
            user_id=user_id,
        )

    return record_id


def create_obligation(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    authorization_fiscal_year: int,
    start_fiscal_year: int,
    end_fiscal_year: int,
    limit_amount_yen: int,
    purpose: str | None = None,
    user_id: int | None = None,
) -> int:
    """Create a draft debt obligation."""

    return _create(
        connection,
        "OBLIGATION",
        project_id=project_id,
        title=title,
        authorization_fiscal_year=authorization_fiscal_year,
        start_fiscal_year=start_fiscal_year,
        end_fiscal_year=end_fiscal_year,
        amount_yen=limit_amount_yen,
        purpose=purpose,
        user_id=user_id,
    )


def create_continuing_expenditure(
    connection: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    start_fiscal_year: int,
    end_fiscal_year: int,
    total_amount_yen: int,
    purpose: str | None = None,
    user_id: int | None = None,
) -> int:
    """Create a draft continuing expenditure."""

    return _create(
        connection,
        "CONTINUING",
        project_id=project_id,
        title=title,
        start_fiscal_year=start_fiscal_year,
        end_fiscal_year=end_fiscal_year,
        amount_yen=total_amount_yen,
        purpose=purpose,
        user_id=user_id,
    )


def set_annual_schedule(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
    schedule: dict[int, int],
    *,
    change_reason: str,
    user_id: int | None = None,
) -> None:
    """Replace a draft's entire annual schedule atomically."""

    spec = _spec(kind)
    _integer(record_id, "record_id", 1)
    reason = _required(change_reason, "change_reason")

    with _transaction(connection):
        previous = _snapshot(connection, kind, record_id)
        record = previous["record"]

        if record[spec["status"]] != "DRAFT":
            raise ValueError(
                "Annual schedule changes require DRAFT status"
            )

        start = record["start_fiscal_year"]
        end = record["end_fiscal_year"]
        total = record[spec["amount"]]

        _validate_years(schedule, start, end)
        _validate_total(
            kind,
            total,
            schedule,
            require_complete=False,
        )

        _replace_years(connection, kind, record_id, schedule)

        _audit(
            connection,
            kind,
            record_id,
            "SCHEDULE_CHANGED",
            previous,
            reason,
            user_id=user_id,
        )


def authorize_multiyear_budget(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
    *,
    authorized_on: str,
    decision_reference: str,
    user_id: int | None = None,
) -> None:
    """Record an authorization decision for a draft."""

    spec = _spec(kind)
    _integer(record_id, "record_id", 1)
    approval_date = _date(authorized_on)
    reference = _required(
        decision_reference,
        "decision_reference",
    )

    with _transaction(connection):
        previous = _snapshot(connection, kind, record_id)
        record = previous["record"]

        if record[spec["status"]] != "DRAFT":
            raise ValueError("Only drafts can be authorized")

        schedule = {
            row["fiscal_year"]: row["amount_yen"]
            for row in previous["years"]
        }

        if not schedule:
            raise ValueError("Annual schedule is required")

        _validate_total(
            kind,
            record[spec["amount"]],
            schedule,
            require_complete=True,
        )

        connection.execute(
            f"UPDATE {spec['table']} "
            f"SET {spec['status']} = 'AUTHORIZED', "
            "authorized_on = ?, "
            "decision_reference = ?, "
            "updated_at = CURRENT_TIMESTAMP "
            f"WHERE {spec['id']} = ?",
            (approval_date, reference, record_id),
        )

        _audit(
            connection,
            kind,
            record_id,
            "AUTHORIZED",
            previous,
            "Authorization recorded",
            decision_reference=reference,
            user_id=user_id,
        )


def revise_multiyear_budget(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
    *,
    new_amount_yen: int,
    new_schedule: dict[int, int],
    revised_on: str,
    decision_reference: str,
    revision_reason: str,
    confirmed: bool = False,
    user_id: int | None = None,
) -> None:
    """Record an approved amendment with before/after history.

    Explicit confirmation and a decision reference are required.
    This does not change the original start/end fiscal years.
    """

    spec = _spec(kind)
    _integer(record_id, "record_id", 1)
    amount = _integer(new_amount_yen, "new_amount_yen")
    revision_date = _date(revised_on)
    reference = _required(
        decision_reference,
        "decision_reference",
    )
    reason = _required(revision_reason, "revision_reason")

    if not confirmed:
        raise ValueError(
            "Revision requires explicit confirmation"
        )

    with _transaction(connection):
        previous = _snapshot(connection, kind, record_id)
        record = previous["record"]

        if record[spec["status"]] not in {
            "AUTHORIZED",
            "REVISED",
        }:
            raise ValueError(
                "Only authorized records can be revised"
            )

        if (
            record["authorized_on"] is not None
            and revision_date < record["authorized_on"]
        ):
            raise ValueError(
                "Revision date precedes recorded authorization"
            )

        _validate_years(
            new_schedule,
            record["start_fiscal_year"],
            record["end_fiscal_year"],
        )

        if not new_schedule:
            raise ValueError("Annual schedule is required")

        _validate_total(
            kind,
            amount,
            new_schedule,
            require_complete=True,
        )

        connection.execute(
            f"UPDATE {spec['table']} "
            f"SET {spec['amount']} = ?, "
            f"{spec['status']} = 'REVISED', "
            "authorized_on = ?, "
            "decision_reference = ?, "
            "updated_at = CURRENT_TIMESTAMP "
            f"WHERE {spec['id']} = ?",
            (amount, revision_date, reference, record_id),
        )

        _replace_years(
            connection,
            kind,
            record_id,
            new_schedule,
        )

        _audit(
            connection,
            kind,
            record_id,
            "REVISED",
            previous,
            reason,
            decision_reference=reference,
            user_id=user_id,
        )


def get_multiyear_budget(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
) -> dict:
    """Return the current record, annual schedule and totals."""

    _integer(record_id, "record_id", 1)
    snapshot = _snapshot(connection, kind, record_id)
    spec = _spec(kind)
    record = snapshot["record"]
    annual_total = sum(
        row["amount_yen"] for row in snapshot["years"]
    )

    return {
        "record_type_code": kind,
        "record_id": record_id,
        "project_id": record["project_id"],
        "title": record[spec["title"]],
        "status_code": record[spec["status"]],
        "start_fiscal_year": record["start_fiscal_year"],
        "end_fiscal_year": record["end_fiscal_year"],
        "limit_or_total_yen": record[spec["amount"]],
        "scheduled_total_yen": annual_total,
        "unallocated_difference_yen": (
            record[spec["amount"]] - annual_total
        ),
        "annual_schedule": snapshot["years"],
        "decision_reference": record["decision_reference"],
    }


def get_multiyear_audit_history(
    connection: sqlite3.Connection,
    kind: str,
    record_id: int,
) -> list[dict]:
    """Read the immutable audit snapshots chronologically."""

    _integer(record_id, "record_id", 1)
    _snapshot(connection, kind, record_id)

    rows = connection.execute(
        """
        SELECT
            audit_id,
            action_code,
            previous_snapshot_json,
            new_snapshot_json,
            change_reason,
            decision_reference,
            created_at
        FROM budget_multiyear_audit
        WHERE record_type_code = ?
          AND record_id = ?
        ORDER BY audit_id
        """,
        (kind, record_id),
    ).fetchall()

    return [
        {
            "audit_id": row[0],
            "action_code": row[1],
            "previous": (
                json.loads(row[2])
                if row[2] is not None
                else None
            ),
            "current": json.loads(row[3]),
            "change_reason": row[4],
            "decision_reference": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]
