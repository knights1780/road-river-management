"""Recorded contract allocations and payments; not official treasury balances.

Every write is an atomic SQLite transaction. Do not update contract/payment
amounts outside the service after allocations have been recorded.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date
from uuid import uuid4


@contextmanager
def _transaction(connection: sqlite3.Connection):
    if connection.in_transaction:
        raise RuntimeError("Finish the existing transaction first")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _id(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _amount(value: int) -> int:
    return _id(value, "amount_yen")


def _year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year is out of range")
    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _date(value: str) -> str:
    text = _text(value, "paid_on")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("paid_on must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError("paid_on must be YYYY-MM-DD")
    return text


def _contract(connection: sqlite3.Connection, work_id: int) -> int:
    row = connection.execute(
        """SELECT COALESCE(final_amount_yen, current_contract_amount_yen)
           FROM construction_works WHERE construction_work_id = ?""",
        (work_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Construction work not found")
    if row[0] is None:
        raise ValueError("Contract amount is not recorded")
    return row[0]


def allocate_contract_to_budget(
    connection: sqlite3.Connection,
    *,
    construction_work_id: int,
    budget_request_id: int,
    amount_yen: int,
    allocation_reason: str,
) -> int:
    """Allocate part of one work's contract to one fiscal budget request."""
    work = _id(construction_work_id, "construction_work_id")
    request = _id(budget_request_id, "budget_request_id")
    amount = _amount(amount_yen)
    reason = _text(allocation_reason, "allocation_reason")

    with _transaction(connection):
        contract_amount = _contract(connection, work)
        linked = connection.execute(
            """SELECT 1 FROM budget_requests AS r
               WHERE r.budget_request_id = ?
                 AND EXISTS (
                     SELECT 1 FROM project_construction_links AS l
                     WHERE l.project_id = r.project_id
                       AND l.construction_work_id = ?
                 )""",
            (request, work),
        ).fetchone()
        if linked is None:
            raise ValueError("Budget project is not linked to construction work")

        allocated = connection.execute(
            """SELECT COALESCE(SUM(allocated_contract_amount_yen), 0)
               FROM budget_contract_allocations
               WHERE construction_work_id = ?""",
            (work,),
        ).fetchone()[0]
        if allocated + amount > contract_amount:
            raise ValueError("Contract allocations exceed contract amount")

        result = connection.execute(
            """INSERT INTO budget_contract_allocations (
                   allocation_uuid, construction_work_id,
                   budget_request_id, allocated_contract_amount_yen,
                   allocation_reason
               ) VALUES (?, ?, ?, ?, ?)""",
            (str(uuid4()), work, request, amount, reason),
        )
        return_id = result.lastrowid
    return return_id


def record_construction_payment(
    connection: sqlite3.Connection,
    *,
    construction_work_id: int,
    payment_reference: str,
    payment_fiscal_year: int,
    paid_on: str,
    paid_amount_yen: int,
    payment_note: str | None = None,
) -> int:
    """Record one actual payment once, independently of its budget allocation."""
    work = _id(construction_work_id, "construction_work_id")
    reference = _text(payment_reference, "payment_reference")
    year = _year(payment_fiscal_year)
    paid_date = _date(paid_on)
    amount = _amount(paid_amount_yen)
    # Japanese fiscal year runs from April through the following March.
    actual_year = int(paid_date[:4]) - (int(paid_date[5:7]) < 4)
    if actual_year != year:
        raise ValueError("paid_on does not belong to payment fiscal year")

    with _transaction(connection):
        contract_amount = _contract(connection, work)
        prior = connection.execute(
            """SELECT COALESCE(SUM(paid_amount_yen), 0)
               FROM construction_payments
               WHERE construction_work_id = ?""",
            (work,),
        ).fetchone()[0]
        if prior + amount > contract_amount:
            raise ValueError("Recorded payments exceed contract amount")
        result = connection.execute(
            """INSERT INTO construction_payments (
                   payment_uuid, construction_work_id, payment_reference,
                   payment_fiscal_year, paid_on, paid_amount_yen, payment_note
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), work, reference, year, paid_date, amount, payment_note),
        )
        payment_id = result.lastrowid
    return payment_id


def allocate_payment_to_budget(
    connection: sqlite3.Connection,
    *,
    payment_id: int,
    budget_request_id: int,
    amount_yen: int,
) -> int:
    payment = _id(payment_id, "payment_id")
    request = _id(budget_request_id, "budget_request_id")
    amount = _amount(amount_yen)
    with _transaction(connection):
        row = connection.execute(
            """SELECT construction_work_id, paid_amount_yen
               FROM construction_payments WHERE payment_id = ?""",
            (payment,),
        ).fetchone()
        if row is None:
            raise ValueError("Payment not found")
        work, paid_total = row
        allocated_to_payment = connection.execute(
            """SELECT COALESCE(SUM(allocated_payment_amount_yen), 0)
               FROM budget_payment_allocations WHERE payment_id = ?""",
            (payment,),
        ).fetchone()[0]
        if allocated_to_payment + amount > paid_total:
            raise ValueError("Payment allocations exceed payment")

        contract_allocation = connection.execute(
            """SELECT allocated_contract_amount_yen
               FROM budget_contract_allocations
               WHERE construction_work_id = ? AND budget_request_id = ?""",
            (work, request),
        ).fetchone()
        if contract_allocation is None:
            raise ValueError("No contract allocation for this budget")
        already_paid = connection.execute(
            """SELECT COALESCE(SUM(allocated_payment_amount_yen), 0)
               FROM budget_payment_allocations
               WHERE construction_work_id = ? AND budget_request_id = ?""",
            (work, request),
        ).fetchone()[0]
        if already_paid + amount > contract_allocation[0]:
            raise ValueError("Payments exceed budget contract allocation")
        result = connection.execute(
            """INSERT INTO budget_payment_allocations (
                   payment_id, construction_work_id, budget_request_id,
                   allocated_payment_amount_yen
               ) VALUES (?, ?, ?, ?)""",
            (payment, work, request, amount),
        )
        allocation_id = result.lastrowid
    return allocation_id


def get_fiscal_execution_report(
    connection: sqlite3.Connection,
    fiscal_year: int,
) -> dict:
    """Budget-origin-year report; paid totals include all subsequent payment years."""
    year = _year(fiscal_year)
    rows = connection.execute(
        """SELECT r.budget_request_id, r.project_id, r.request_title,
                  COALESCE(a.appropriated_yen, 0),
                  COALESCE(c.contract_yen, 0),
                  COALESCE(p.paid_yen, 0)
           FROM budget_requests AS r
           LEFT JOIN (
               SELECT x.budget_request_id,
                      SUM(x.appropriated_amount_yen) AS appropriated_yen
               FROM budget_appropriations AS x
               GROUP BY x.budget_request_id
           ) AS a ON a.budget_request_id = r.budget_request_id
           LEFT JOIN (
               SELECT budget_request_id,
                      SUM(allocated_contract_amount_yen) AS contract_yen
               FROM budget_contract_allocations GROUP BY budget_request_id
           ) AS c ON c.budget_request_id = r.budget_request_id
           LEFT JOIN (
               SELECT budget_request_id,
                      SUM(allocated_payment_amount_yen) AS paid_yen
               FROM budget_payment_allocations GROUP BY budget_request_id
           ) AS p ON p.budget_request_id = r.budget_request_id
           WHERE r.fiscal_year = ?
           ORDER BY r.budget_request_id""",
        (year,),
    ).fetchall()
    # Adjustments are aggregated per budget request, independently of appropriation rows.
    adjustment_totals = dict(connection.execute(
        """SELECT p.budget_request_id, SUM(a.amount_delta_yen)
           FROM budget_appropriation_adjustments AS a
           JOIN budget_appropriations AS p
             ON p.budget_appropriation_id = a.budget_appropriation_id
           GROUP BY p.budget_request_id"""
    ).fetchall())
    requests = [
        {
            "budget_request_id": row[0],
            "project_id": row[1],
            "request_title": row[2],
            "effective_appropriation_yen": row[3] + adjustment_totals.get(row[0], 0),
            "allocated_contract_yen": row[4],
            "allocated_payment_yen_all_years": row[5],
        }
        for row in rows
    ]
    return {
        "fiscal_year": year,
        "request_count": len(requests),
        "total_effective_appropriation_yen": sum(r["effective_appropriation_yen"] for r in requests),
        "total_allocated_contract_yen": sum(r["allocated_contract_yen"] for r in requests),
        "total_allocated_payment_yen_all_years": sum(r["allocated_payment_yen_all_years"] for r in requests),
        "requests": requests,
    }


def get_payment_year_report(connection: sqlite3.Connection, fiscal_year: int) -> dict:
    """Physical payments paid in this fiscal year; count each payment only once."""
    year = _year(fiscal_year)
    row = connection.execute(
        """SELECT COUNT(*), COALESCE(SUM(paid_amount_yen), 0)
           FROM construction_payments WHERE payment_fiscal_year = ?""",
        (year,),
    ).fetchone()
    allocated = connection.execute(
        """SELECT COALESCE(SUM(a.allocated_payment_amount_yen), 0)
           FROM budget_payment_allocations AS a
           JOIN construction_payments AS p ON p.payment_id = a.payment_id
           WHERE p.payment_fiscal_year = ?""",
        (year,),
    ).fetchone()[0]
    return {
        "fiscal_year": year,
        "payment_count": row[0],
        "paid_total_yen": row[1],
        "allocated_payment_yen": allocated,
        "unallocated_payment_yen": row[1] - allocated,
    }
