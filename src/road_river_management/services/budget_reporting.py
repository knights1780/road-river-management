
"""Read-only fiscal budget reports and project progress integration.

Requires SQLite migration 0011.

Budget amounts are integer yen.
Only the latest assessment round is used for report totals.
Historical assessment records remain unchanged.

No official progress values are modified.
"""

import sqlite3

from road_river_management.services.progress_reference import (
    calculate_project_reference_progress,
)

FINAL_ASSESSMENT_RESULTS = {"FULL", "REDUCED", "ZERO"}
ASSESSMENT_RESULTS = {
    "FULL",
    "REDUCED",
    "ZERO",
    "HELD",
    "REJECTED",
}


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _fiscal_year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year is out of range")
    return value


def _project(
    connection: sqlite3.Connection,
    project_id: int,
) -> tuple:
    _positive_id(project_id, "project_id")
    row = connection.execute(
        """
        SELECT
            project_id,
            project_name,
            status_code,
            official_progress_percent
        FROM maintenance_projects
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Project not found: {project_id}")
    return row


def _request_rows(
    connection: sqlite3.Connection,
    *,
    fiscal_year: int | None = None,
    project_id: int | None = None,
) -> list[dict]:
    """Fetch one row per request, not per assessment or appropriation."""

    rows = connection.execute(
        """
        SELECT
            r.budget_request_id,
            r.project_id,
            r.stage_id,
            r.fiscal_year,
            r.request_title,
            r.request_status_code,
            r.previous_request_id,
            r.original_requested_amount_yen,
            r.current_requested_amount_yen,
            a.assessment_round,
            a.assessment_result_code,
            a.requested_amount_at_assessment_yen,
            a.assessed_amount_yen,
            COALESCE(p.total_amount_yen, 0)
        FROM budget_requests AS r
        LEFT JOIN budget_assessments AS a
          ON a.budget_assessment_id = (
              SELECT a2.budget_assessment_id
              FROM budget_assessments AS a2
              WHERE a2.budget_request_id = r.budget_request_id
              ORDER BY
                  a2.assessment_round DESC,
                  a2.budget_assessment_id DESC
              LIMIT 1
          )
        LEFT JOIN (
            SELECT
                budget_request_id,
                SUM(appropriated_amount_yen) AS total_amount_yen
            FROM budget_appropriations
            GROUP BY budget_request_id
        ) AS p
          ON p.budget_request_id = r.budget_request_id
        WHERE (? IS NULL OR r.fiscal_year = ?)
          AND (? IS NULL OR r.project_id = ?)
        ORDER BY
            r.fiscal_year,
            r.project_id,
            r.budget_request_id
        """,
        (
            fiscal_year,
            fiscal_year,
            project_id,
            project_id,
        ),
    ).fetchall()

    result = []
    for row in rows:
        (
            request_id,
            row_project_id,
            stage_id,
            row_year,
            title,
            status,
            previous_id,
            original_amount,
            current_amount,
            assessment_round,
            assessment_result,
            assessed_requested_amount,
            assessed_amount,
            appropriation_amount,
        ) = row

        result.append(
            {
                "budget_request_id": request_id,
                "project_id": row_project_id,
                "stage_id": stage_id,
                "fiscal_year": row_year,
                "request_title": title,
                "request_status_code": status,
                "previous_request_id": previous_id,
                "is_followup": previous_id is not None,
                "original_requested_amount_yen": original_amount,
                "current_requested_amount_yen": current_amount,
                "latest_assessment_round": assessment_round,
                "latest_assessment_result": assessment_result,
                "requested_amount_at_assessment_yen": (
                    assessed_requested_amount
                ),
                "latest_assessed_amount_yen": assessed_amount,
                "total_appropriated_amount_yen": (
                    appropriation_amount
                ),
            }
        )
    return result


def _summarize(rows: list[dict]) -> dict:
    """Aggregate independent request records without join multiplication."""

    counts = {
        "UNASSESSED": 0,
        "FULL": 0,
        "REDUCED": 0,
        "ZERO": 0,
        "HELD": 0,
        "REJECTED": 0,
    }

    requested_total = 0
    assessed_total = 0
    finalized_requested_total = 0
    finalized_assessed_total = 0
    appropriated_total = 0
    followup_count = 0
    finalized_count = 0

    for row in rows:
        requested_total += row["current_requested_amount_yen"]
        appropriated_total += row[
            "total_appropriated_amount_yen"
        ]

        if row["is_followup"]:
            followup_count += 1

        result = row["latest_assessment_result"]

        if result is None:
            counts["UNASSESSED"] += 1
            continue

        counts[result] += 1

        assessed_amount = row["latest_assessed_amount_yen"]

        if assessed_amount is not None:
            assessed_total += assessed_amount

        if result in FINAL_ASSESSMENT_RESULTS:
            finalized_count += 1
            finalized_requested_total += row[
                "requested_amount_at_assessment_yen"
            ]
            finalized_assessed_total += assessed_amount

    return {
        "request_count": len(rows),
        "followup_request_count": followup_count,
        "assessment_counts": counts,
        "total_requested_amount_yen": requested_total,
        "total_known_assessed_amount_yen": assessed_total,
        "finalized_assessment_count": finalized_count,
        "finalized_requested_amount_yen": (
            finalized_requested_total
        ),
        "finalized_assessed_amount_yen": finalized_assessed_total,
        "finalized_assessment_reduction_yen": (
            finalized_requested_total - finalized_assessed_total
        ),
        "total_appropriated_amount_yen": appropriated_total,
    }


def get_fiscal_budget_report(
    connection: sqlite3.Connection,
    fiscal_year: int,
    *,
    project_id: int | None = None,
) -> dict:
    """Return a fiscal-year budget dashboard report.

    Known assessment totals exclude unknown (NULL) amounts.
    Reduction is calculated only for FULL, REDUCED and ZERO
    assessments, using the requested amount captured at assessment.
    """

    year = _fiscal_year(fiscal_year)

    if project_id is not None:
        _project(connection, project_id)

    requests = _request_rows(
        connection,
        fiscal_year=year,
        project_id=project_id,
    )

    summary = _summarize(requests)

    project_ids = sorted(
        {row["project_id"] for row in requests}
    )

    projects = []
    for item_project_id in project_ids:
        project_rows = [
            row
            for row in requests
            if row["project_id"] == item_project_id
        ]
        project_record = _project(connection, item_project_id)

        projects.append(
            {
                "project_id": item_project_id,
                "project_name": project_record[1],
                "summary": _summarize(project_rows),
            }
        )

    return {
        "fiscal_year": year,
        "project_id": project_id,
        **summary,
        "projects": projects,
        "requests": requests,
    }


def get_project_budget_progress_report(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    fiscal_year: int | None = None,
) -> dict:
    """Show budget requests alongside independent project progress.

    Project progress is reported for the project as a whole.
    It is not recalculated from budget amounts.
    """

    project = _project(connection, project_id)

    if fiscal_year is not None:
        _fiscal_year(fiscal_year)

    rows = _request_rows(
        connection,
        fiscal_year=fiscal_year,
        project_id=project_id,
    )

    years = sorted({row["fiscal_year"] for row in rows})

    yearly_summaries = []
    for year in years:
        year_rows = [
            row for row in rows if row["fiscal_year"] == year
        ]
        yearly_summaries.append(
            {
                "fiscal_year": year,
                **_summarize(year_rows),
            }
        )

    return {
        "project_id": project_id,
        "project_name": project[1],
        "project_status_code": project[2],
        "official_progress_percent": project[3],
        "reference_progress_percent": (
            calculate_project_reference_progress(
                connection,
                project_id,
            )
        ),
        "fiscal_year": fiscal_year,
        "budget_summary": _summarize(rows),
        "yearly_summaries": yearly_summaries,
        "requests": rows,
    }


def get_budget_request_chain(
    connection: sqlite3.Connection,
    budget_request_id: int,
) -> list[dict]:
    """Return previous requests, oldest first, through the target."""

    request_id = _positive_id(
        budget_request_id,
        "budget_request_id",
    )

    visited = set()
    chain = []
    current_id = request_id

    while current_id is not None:
        if current_id in visited:
            raise ValueError(
                "Cyclic budget request history detected"
            )

        visited.add(current_id)

        row = connection.execute(
            """
            SELECT
                budget_request_id,
                project_id,
                fiscal_year,
                previous_request_id,
                request_status_code,
                current_requested_amount_yen
            FROM budget_requests
            WHERE budget_request_id = ?
            """,
            (current_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                f"Budget request not found: {current_id}"
            )

        chain.append(
            {
                "budget_request_id": row[0],
                "project_id": row[1],
                "fiscal_year": row[2],
                "previous_request_id": row[3],
                "request_status_code": row[4],
                "current_requested_amount_yen": row[5],
            }
        )

        current_id = row[3]

    chain.reverse()
    return chain
