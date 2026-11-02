
"""Read-only budget, contract, and payment dashboard.

Requires migrations 0011, 0012 and 0014.
All monetary amounts are recorded integer yen, not certified
treasury balances or legally available budget balances.
"""

import sqlite3

from road_river_management.services.budget_execution import (
    get_fiscal_execution_report,
    get_payment_year_report,
)


def _year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year is out of range")
    return value


def _project_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer")
    if value <= 0:
        raise ValueError("project_id must be positive")
    return value


def _projects(
    connection: sqlite3.Connection,
) -> dict[int, str]:
    return dict(
        connection.execute(
            """
            SELECT project_id, project_name
            FROM maintenance_projects
            """
        ).fetchall()
    )


def _carryover_totals(
    connection: sqlite3.Connection,
    fiscal_year: int,
    project_id: int | None,
) -> dict:
    rows = connection.execute(
        """
        SELECT
            c.carryover_type_code,
            COUNT(*),
            COALESCE(SUM(c.actual_carryover_amount_yen), 0)
        FROM budget_carryovers AS c
        JOIN budget_requests AS r
          ON r.budget_request_id = c.budget_request_id
        WHERE c.carryover_status_code = 'FINALIZED'
          AND c.source_fiscal_year = ?
          AND (? IS NULL OR r.project_id = ?)
        GROUP BY c.carryover_type_code
        """,
        (fiscal_year, project_id, project_id),
    ).fetchall()

    by_type = {
        "MEIKYO": {"count": 0, "amount_yen": 0},
        "ACCIDENT": {"count": 0, "amount_yen": 0},
    }

    for kind, count, amount in rows:
        by_type[kind] = {
            "count": count,
            "amount_yen": amount,
        }

    return {
        "total_finalized_carryover_yen": sum(
            item["amount_yen"] for item in by_type.values()
        ),
        "by_type": by_type,
    }


def _payment_year_totals(
    connection: sqlite3.Connection,
    fiscal_year: int,
    project_id: int | None,
) -> dict:
    """Count payment originals once, without allocation joins."""

    if project_id is None:
        return get_payment_year_report(connection, fiscal_year)

    # Project-specific payment figures are *allocated shares*.
    # Whole-work payment totals would be duplicated where a work
    # is linked to multiple projects.
    row = connection.execute(
        """
        SELECT
            COUNT(DISTINCT p.payment_id),
            COALESCE(SUM(a.allocated_payment_amount_yen), 0)
        FROM budget_payment_allocations AS a
        JOIN construction_payments AS p
          ON p.payment_id = a.payment_id
        JOIN budget_requests AS r
          ON r.budget_request_id = a.budget_request_id
        WHERE p.payment_fiscal_year = ?
          AND r.project_id = ?
        """,
        (fiscal_year, project_id),
    ).fetchone()

    return {
        "fiscal_year": fiscal_year,
        "payment_count_with_project_allocation": row[0],
        "project_allocated_payment_yen": row[1],
        "scope": "PROJECT_ALLOCATIONS_ONLY",
    }


def _linked_contracts(
    connection: sqlite3.Connection,
    request_ids: list[int],
) -> dict:
    """Return distinct works funded by the selected requests.

    A work is counted once even when allocated to several
    budget requests or linked through multiple project stages.
    """

    if not request_ids:
        return {
            "linked_work_count": 0,
            "linked_work_contract_total_yen": 0,
            "linked_work_contract_unknown_count": 0,
        }

    placeholders = ", ".join("?" for _ in request_ids)

    rows = connection.execute(
        f"""
        SELECT DISTINCT
            w.construction_work_id,
            COALESCE(
                w.final_amount_yen,
                w.current_contract_amount_yen
            )
        FROM construction_works AS w
        JOIN budget_contract_allocations AS a
          ON a.construction_work_id = w.construction_work_id
        WHERE a.budget_request_id IN ({placeholders})
        """,
        request_ids,
    ).fetchall()

    return {
        "linked_work_count": len(rows),
        "linked_work_contract_total_yen": sum(
            amount for _, amount in rows if amount is not None
        ),
        "linked_work_contract_unknown_count": sum(
            amount is None for _, amount in rows
        ),
    }


def get_integrated_fiscal_dashboard(
    connection: sqlite3.Connection,
    fiscal_year: int,
    *,
    project_id: int | None = None,
) -> dict:
    """Produce a budget-year dashboard and payment-year context.

    Effective appropriations are recorded budget totals.
    Allocated contract and payment amounts are not added to
    those totals, and no legal available-balance figure is
    calculated.
    """

    year = _year(fiscal_year)
    selected_project = _project_id(project_id)

    project_names = _projects(connection)
    if (
        selected_project is not None
        and selected_project not in project_names
    ):
        raise ValueError("Project not found")

    fiscal = get_fiscal_execution_report(connection, year)
    requests = fiscal["requests"]

    if selected_project is not None:
        requests = [
            item for item in requests
            if item["project_id"] == selected_project
        ]

    request_total = sum(
        item["effective_appropriation_yen"]
        for item in requests
    )
    contract_allocated = sum(
        item["allocated_contract_yen"]
        for item in requests
    )
    payments_allocated = sum(
        item["allocated_payment_yen_all_years"]
        for item in requests
    )

    project_groups = {}
    for item in requests:
        pid = item["project_id"]
        if pid not in project_groups:
            project_groups[pid] = {
                "project_id": pid,
                "project_name": project_names[pid],
                "budget_request_count": 0,
                "effective_appropriation_yen": 0,
                "allocated_contract_yen": 0,
                "allocated_payment_yen_all_years": 0,
            }

        group = project_groups[pid]
        group["budget_request_count"] += 1
        group["effective_appropriation_yen"] += item[
            "effective_appropriation_yen"
        ]
        group["allocated_contract_yen"] += item[
            "allocated_contract_yen"
        ]
        group["allocated_payment_yen_all_years"] += item[
            "allocated_payment_yen_all_years"
        ]

    return {
        "fiscal_year": year,
        "project_id": selected_project,
        "scope": "RECORDED_OPERATIONAL_DATA",
        "budget_year": {
            "request_count": len(requests),
            "effective_appropriation_yen": request_total,
            "allocated_contract_yen": contract_allocated,
            "allocated_payment_yen_all_years": payments_allocated,
            "unallocated_budget_to_contract_yen_reference": (
                request_total - contract_allocated
            ),
            "unpaid_allocated_contract_yen_reference": (
                contract_allocated - payments_allocated
            ),
        },
        "payment_year": _payment_year_totals(
            connection, year, selected_project
        ),
        "carryovers_from_year": _carryover_totals(
            connection, year, selected_project
        ),
        "linked_contracts": _linked_contracts(
            connection,
            [item["budget_request_id"] for item in requests],
        ),
        "projects": [
            project_groups[pid]
            for pid in sorted(project_groups)
        ],
        "requests": requests,
    }
