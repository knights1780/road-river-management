
"""Budget adjustments and carryover workflow services.

Requires SQLite migrations 0011 and 0012.

Financial amounts are integer Japanese yen.

This service records administrative decisions.
It does not determine legal eligibility or calculate
the legally available unspent appropriation balance.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date
from uuid import uuid4


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


def _id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _money(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be integer yen")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _required(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _date(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a date string")

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be a valid YYYY-MM-DD date"
        ) from exc

    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be YYYY-MM-DD")

    return value


def _event(
    connection: sqlite3.Connection,
    request_id: int,
    description: str,
    user_id: int | None = None,
) -> None:
    """Record a linked administrative event in migration 0011."""

    connection.execute(
        """
        INSERT INTO budget_request_events (
            budget_request_id,
            event_type_code,
            event_description,
            acted_by_user_id
        )
        VALUES (?, 'COMMENT', ?, ?)
        """,
        (request_id, description, user_id),
    )


def _effective_appropriation(
    connection: sqlite3.Connection,
    request_id: int,
) -> int:
    """Return recorded appropriations plus all adjustment deltas."""

    base = connection.execute(
        """
        SELECT COALESCE(SUM(appropriated_amount_yen), 0)
        FROM budget_appropriations
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    deltas = connection.execute(
        """
        SELECT COALESCE(SUM(a.amount_delta_yen), 0)
        FROM budget_appropriation_adjustments AS a
        JOIN budget_appropriations AS p
          ON p.budget_appropriation_id =
             a.budget_appropriation_id
        WHERE p.budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    return base + deltas


def get_effective_appropriation(
    connection: sqlite3.Connection,
    budget_request_id: int,
) -> dict:
    """Read the recorded appropriation total after adjustments."""

    request_id = _id(
        budget_request_id, "budget_request_id"
    )
    exists = connection.execute(
        """
        SELECT 1 FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()
    if exists is None:
        raise ValueError("Budget request not found")

    original = connection.execute(
        """
        SELECT COALESCE(SUM(appropriated_amount_yen), 0)
        FROM budget_appropriations
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()[0]

    effective = _effective_appropriation(
        connection, request_id
    )

    return {
        "budget_request_id": request_id,
        "recorded_appropriations_yen": original,
        "net_adjustments_yen": effective - original,
        "effective_appropriation_yen": effective,
    }


def record_appropriation_adjustment(
    connection: sqlite3.Connection,
    budget_appropriation_id: int,
    *,
    adjustment_type_code: str,
    amount_delta_yen: int,
    approved_on: str,
    adjustment_reason: str,
    decision_reference: str | None = None,
    recorded_by_user_id: int | None = None,
) -> int:
    """Append a change without overwriting the original amount."""

    appropriation_id = _id(
        budget_appropriation_id,
        "budget_appropriation_id",
    )
    reason = _required(
        adjustment_reason, "adjustment_reason"
    )
    approved_date = _date(approved_on, "approved_on")

    if isinstance(amount_delta_yen, bool) or not isinstance(
        amount_delta_yen, int
    ):
        raise TypeError("amount_delta_yen must be integer yen")

    if adjustment_type_code not in {
        "INCREASE",
        "DECREASE",
        "CORRECTION",
    }:
        raise ValueError("Invalid adjustment type")

    if amount_delta_yen == 0:
        raise ValueError("Adjustment must not be zero")

    if (
        adjustment_type_code == "INCREASE"
        and amount_delta_yen < 0
    ):
        raise ValueError("INCREASE requires a positive delta")

    if (
        adjustment_type_code == "DECREASE"
        and amount_delta_yen > 0
    ):
        raise ValueError("DECREASE requires a negative delta")

    with _transaction(connection):
        appropriation = connection.execute(
            """
            SELECT budget_request_id
            FROM budget_appropriations
            WHERE budget_appropriation_id = ?
            """,
            (appropriation_id,),
        ).fetchone()

        if appropriation is None:
            raise ValueError("Appropriation not found")

        request_id = appropriation[0]

        new_total = (
            _effective_appropriation(connection, request_id)
            + amount_delta_yen
        )
        if new_total < 0:
            raise ValueError(
                "Effective appropriation cannot be negative"
            )

        finalized = connection.execute(
            """
            SELECT COALESCE(
                SUM(actual_carryover_amount_yen), 0
            )
            FROM budget_carryovers
            WHERE budget_request_id = ?
              AND carryover_status_code = 'FINALIZED'
            """,
            (request_id,),
        ).fetchone()[0]

        if new_total < finalized:
            raise ValueError(
                "Adjustment would fall below recorded "
                "finalized carryovers"
            )

        number = connection.execute(
            """
            SELECT COALESCE(MAX(adjustment_number), 0) + 1
            FROM budget_appropriation_adjustments
            WHERE budget_appropriation_id = ?
            """,
            (appropriation_id,),
        ).fetchone()[0]

        adjustment_id = connection.execute(
            """
            INSERT INTO budget_appropriation_adjustments (
                adjustment_uuid,
                budget_appropriation_id,
                adjustment_number,
                adjustment_type_code,
                amount_delta_yen,
                approved_on,
                decision_reference,
                adjustment_reason,
                recorded_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                appropriation_id,
                number,
                adjustment_type_code,
                amount_delta_yen,
                approved_date,
                decision_reference,
                reason,
                recorded_by_user_id,
            ),
        ).lastrowid

        _event(
            connection,
            request_id,
            (
                f"Appropriation adjustment #{number}: "
                f"{adjustment_type_code}, "
                f"{amount_delta_yen} yen. {reason}"
            ),
            recorded_by_user_id,
        )

    return adjustment_id


def create_carryover(
    connection: sqlite3.Connection,
    budget_request_id: int,
    *,
    carryover_type_code: str,
    carryover_reason: str,
    unavoidable_accident_description: str | None = None,
    obligation_incurred_on: str | None = None,
    notes: str | None = None,
    recorded_by_user_id: int | None = None,
) -> int:
    """Create a draft for the following fiscal year."""

    request_id = _id(
        budget_request_id, "budget_request_id"
    )
    reason = _required(
        carryover_reason, "carryover_reason"
    )

    if carryover_type_code not in {"MEIKYO", "ACCIDENT"}:
        raise ValueError("Invalid carryover type")

    if carryover_type_code == "ACCIDENT":
        _required(
            unavoidable_accident_description,
            "unavoidable_accident_description",
        )
        _date(
            obligation_incurred_on,
            "obligation_incurred_on",
        )
    elif (
        unavoidable_accident_description is not None
        or obligation_incurred_on is not None
    ):
        raise ValueError(
            "Accident-specific details require ACCIDENT type"
        )

    with _transaction(connection):
        request = connection.execute(
            """
            SELECT fiscal_year
            FROM budget_requests
            WHERE budget_request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if request is None:
            raise ValueError("Budget request not found")

        source_year = request[0]

        if source_year >= 2199:
            raise ValueError(
                "Source fiscal year exceeds supported range"
            )

        carryover_id = connection.execute(
            """
            INSERT INTO budget_carryovers (
                carryover_uuid,
                budget_request_id,
                carryover_type_code,
                source_fiscal_year,
                destination_fiscal_year,
                carryover_reason,
                unavoidable_accident_description,
                obligation_incurred_on,
                notes,
                recorded_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                request_id,
                carryover_type_code,
                source_year,
                source_year + 1,
                reason,
                unavoidable_accident_description,
                obligation_incurred_on,
                notes,
                recorded_by_user_id,
            ),
        ).lastrowid

        _event(
            connection,
            request_id,
            (
                f"Carryover draft #{carryover_id}: "
                f"{carryover_type_code}. {reason}"
            ),
            recorded_by_user_id,
        )

    return carryover_id


def authorize_carryover(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    authorized_limit_yen: int,
    authorization_date: str,
    decision_reference: str,
    recorded_by_user_id: int | None = None,
) -> None:
    """Record authorization of an existing draft."""

    cid = _id(carryover_id, "carryover_id")
    limit = _money(
        authorized_limit_yen, "authorized_limit_yen"
    )
    approved_date = _date(
        authorization_date, "authorization_date"
    )
    reference = _required(
        decision_reference, "decision_reference"
    )

    with _transaction(connection):
        row = connection.execute(
            """
            SELECT budget_request_id, carryover_status_code
            FROM budget_carryovers
            WHERE carryover_id = ?
            """,
            (cid,),
        ).fetchone()

        if row is None:
            raise ValueError("Carryover not found")
        request_id, status = row

        if status != "DRAFT":
            raise ValueError(
                "Only draft carryovers can be authorized"
            )

        connection.execute(
            """
            UPDATE budget_carryovers
            SET carryover_status_code = 'AUTHORIZED',
                authorized_limit_yen = ?,
                authorization_date = ?,
                decision_reference = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE carryover_id = ?
            """,
            (limit, approved_date, reference, cid),
        )

        _event(
            connection,
            request_id,
            (
                f"Carryover #{cid} authorized: "
                f"limit {limit} yen, {reference}"
            ),
            recorded_by_user_id,
        )


def finalize_carryover(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    actual_carryover_amount_yen: int,
    finalization_date: str,
    recorded_by_user_id: int | None = None,
) -> None:
    """Record the confirmed carryover amount.

    The appropriation comparison is only a conservative
    consistency check. It is not a calculation of the
    legally available unspent balance.
    """

    cid = _id(carryover_id, "carryover_id")
    amount = _money(
        actual_carryover_amount_yen,
        "actual_carryover_amount_yen",
    )
    finalized_date = _date(
        finalization_date, "finalization_date"
    )

    with _transaction(connection):
        row = connection.execute(
            """
            SELECT
                budget_request_id,
                carryover_status_code,
                authorized_limit_yen,
                authorization_date
            FROM budget_carryovers
            WHERE carryover_id = ?
            """,
            (cid,),
        ).fetchone()

        if row is None:
            raise ValueError("Carryover not found")

        request_id, status, limit, authorized_date = row

        if status != "AUTHORIZED":
            raise ValueError(
                "Only authorized carryovers can be finalized"
            )

        if limit is None or amount > limit:
            raise ValueError(
                "Carryover amount exceeds authorized limit"
            )

        if finalized_date < authorized_date:
            raise ValueError(
                "Finalization cannot precede authorization"
            )

        recorded_total = _effective_appropriation(
            connection, request_id
        )

        previous_finalized = connection.execute(
            """
            SELECT COALESCE(
                SUM(actual_carryover_amount_yen), 0
            )
            FROM budget_carryovers
            WHERE budget_request_id = ?
              AND carryover_status_code = 'FINALIZED'
            """,
            (request_id,),
        ).fetchone()[0]

        if previous_finalized + amount > recorded_total:
            raise ValueError(
                "Carryover totals exceed recorded appropriation"
            )

        connection.execute(
            """
            UPDATE budget_carryovers
            SET carryover_status_code = 'FINALIZED',
                actual_carryover_amount_yen = ?,
                finalization_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE carryover_id = ?
            """,
            (amount, finalized_date, cid),
        )

        _event(
            connection,
            request_id,
            (
                f"Carryover #{cid} finalized: "
                f"{amount} yen"
            ),
            recorded_by_user_id,
        )


def cancel_carryover(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    cancellation_reason: str,
    recorded_by_user_id: int | None = None,
) -> None:
    """Cancel a draft or authorized carryover, retaining history."""

    cid = _id(carryover_id, "carryover_id")
    reason = _required(
        cancellation_reason, "cancellation_reason"
    )

    with _transaction(connection):
        row = connection.execute(
            """
            SELECT budget_request_id, carryover_status_code
            FROM budget_carryovers
            WHERE carryover_id = ?
            """,
            (cid,),
        ).fetchone()

        if row is None:
            raise ValueError("Carryover not found")

        request_id, status = row

        if status not in {"DRAFT", "AUTHORIZED"}:
            raise ValueError(
                "Carryover cannot be cancelled in this status"
            )

        connection.execute(
            """
            UPDATE budget_carryovers
            SET carryover_status_code = 'CANCELLED',
                updated_at = CURRENT_TIMESTAMP
            WHERE carryover_id = ?
            """,
            (cid,),
        )

        _event(
            connection,
            request_id,
            f"Carryover #{cid} cancelled: {reason}",
            recorded_by_user_id,
        )


def get_carryover_summary(
    connection: sqlite3.Connection,
    budget_request_id: int,
) -> dict:
    """Read carryover records and finalized recorded totals."""

    request_id = _id(
        budget_request_id, "budget_request_id"
    )

    exists = connection.execute(
        """
        SELECT 1 FROM budget_requests
        WHERE budget_request_id = ?
        """,
        (request_id,),
    ).fetchone()

    if exists is None:
        raise ValueError("Budget request not found")

    rows = connection.execute(
        """
        SELECT
            carryover_id,
            carryover_type_code,
            source_fiscal_year,
            destination_fiscal_year,
            carryover_status_code,
            authorized_limit_yen,
            actual_carryover_amount_yen,
            carryover_reason,
            decision_reference
        FROM budget_carryovers
        WHERE budget_request_id = ?
        ORDER BY carryover_id
        """,
        (request_id,),
    ).fetchall()

    items = [
        {
            "carryover_id": row[0],
            "carryover_type_code": row[1],
            "source_fiscal_year": row[2],
            "destination_fiscal_year": row[3],
            "status_code": row[4],
            "authorized_limit_yen": row[5],
            "actual_carryover_amount_yen": row[6],
            "carryover_reason": row[7],
            "decision_reference": row[8],
        }
        for row in rows
    ]

    return {
        "budget_request_id": request_id,
        "carryover_count": len(items),
        "finalized_total_yen": sum(
            item["actual_carryover_amount_yen"]
            for item in items
            if item["status_code"] == "FINALIZED"
        ),
        "carryovers": items,
    }
