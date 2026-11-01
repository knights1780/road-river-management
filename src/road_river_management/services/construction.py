
"""Shared construction works and facility history services."""

import sqlite3
from collections.abc import Iterable
from uuid import uuid4


def _require_no_transaction(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError(
            "Commit or rollback the existing transaction first"
        )


def _require_non_negative_amount(
    amount: int | None,
    name: str,
) -> None:
    if amount is None:
        return
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError(f"{name} must be an integer")
    if amount < 0:
        raise ValueError(f"{name} must be non-negative")


def _unique_ids(values: Iterable[int], name: str) -> list[int]:
    result = list(values)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in result):
        raise TypeError(f"{name} must contain integer IDs")
    if any(v <= 0 for v in result):
        raise ValueError(f"{name} must contain positive IDs")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} contains duplicate IDs")
    return result


def _require_existing_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    values: list[int],
) -> None:
    # Only fixed, internal table/column names are supplied here.
    if not values:
        return

    placeholders = ", ".join("?" for _ in values)
    rows = connection.execute(
        f"SELECT {column} FROM {table} "
        f"WHERE {column} IN ({placeholders})",
        values,
    ).fetchall()

    found = {row[0] for row in rows}
    missing = set(values) - found
    if missing:
        raise ValueError(
            f"Unknown {table} IDs: {sorted(missing)}"
        )


def create_construction_work(
    connection: sqlite3.Connection,
    construction_name: str,
    *,
    facility_ids: Iterable[int],
    component_ids: Iterable[int] = (),
    construction_code: str | None = None,
    current_contract_amount_yen: int | None = None,
    contractor_organization_id: int | None = None,
    planned_start_on: str | None = None,
    planned_end_on: str | None = None,
    construction_description: str | None = None,
) -> int:
    """Create one construction work and its facility/component links.

    This function owns and commits its transaction.
    It does not write to facility_construction_history.
    """
    if not isinstance(construction_name, str):
        raise TypeError("construction_name must be a string")

    name = construction_name.strip()
    if not name:
        raise ValueError("construction_name is required")

    facilities = _unique_ids(facility_ids, "facility_ids")
    components = _unique_ids(component_ids, "component_ids")

    if not facilities:
        raise ValueError("At least one facility is required")

    _require_non_negative_amount(
        current_contract_amount_yen,
        "current_contract_amount_yen",
    )
    _require_no_transaction(connection)

    connection.execute("BEGIN IMMEDIATE")
    try:
        _require_existing_ids(
            connection,
            "managed_facilities",
            "facility_id",
            facilities,
        )

        _require_existing_ids(
            connection,
            "facility_components",
            "component_id",
            components,
        )

        if components:
            placeholders = ", ".join("?" for _ in components)
            rows = connection.execute(
                "SELECT component_id, facility_id "
                "FROM facility_components "
                f"WHERE component_id IN ({placeholders})",
                components,
            ).fetchall()

            facilities_set = set(facilities)
            for component_id, facility_id in rows:
                if facility_id not in facilities_set:
                    raise ValueError(
                        f"Component {component_id} belongs to "
                        f"unselected facility {facility_id}"
                    )

        cursor = connection.execute(
            """
            INSERT INTO construction_works (
                construction_uuid,
                construction_code,
                construction_name,
                current_contract_amount_yen,
                contractor_organization_id,
                planned_start_on,
                planned_end_on,
                construction_description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                construction_code,
                name,
                current_contract_amount_yen,
                contractor_organization_id,
                planned_start_on,
                planned_end_on,
                construction_description,
            ),
        )
        work_id = cursor.lastrowid

        for facility_id in facilities:
            connection.execute(
                """
                INSERT INTO construction_work_facilities (
                    construction_work_id, facility_id
                )
                VALUES (?, ?)
                """,
                (work_id, facility_id),
            )

        for component_id in components:
            connection.execute(
                """
                INSERT INTO construction_work_components (
                    construction_work_id, component_id
                )
                VALUES (?, ?)
                """,
                (work_id, component_id),
            )

        connection.commit()
        return work_id

    except Exception:
        connection.rollback()
        raise


def link_construction_to_project(
    connection: sqlite3.Connection,
    construction_work_id: int,
    project_id: int,
    stage_id: int,
    *,
    progress_weight: float = 1.0,
) -> int:
    """Link an existing work to a construction stage."""
    if isinstance(progress_weight, bool) or not isinstance(
        progress_weight, (int, float)
    ):
        raise TypeError("progress_weight must be numeric")
    if not 0 <= progress_weight < float("inf"):
        raise ValueError("progress_weight must be finite and non-negative")

    _require_no_transaction(connection)
    connection.execute("BEGIN IMMEDIATE")

    try:
        stage = connection.execute(
            """
            SELECT stage_type_code
            FROM project_stages
            WHERE project_id = ? AND stage_id = ?
            """,
            (project_id, stage_id),
        ).fetchone()

        if stage is None:
            raise ValueError("Stage does not belong to project")

        if stage[0] != "CONSTRUCTION":
            raise ValueError("Only CONSTRUCTION stages can link works")

        cursor = connection.execute(
            """
            INSERT INTO project_construction_links (
                project_id,
                stage_id,
                construction_work_id,
                progress_weight
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                project_id,
                stage_id,
                construction_work_id,
                progress_weight,
            ),
        )

        connection.commit()
        return cursor.lastrowid

    except Exception:
        connection.rollback()
        raise


def get_facility_construction_history(
    connection: sqlite3.Connection,
    facility_id: int,
) -> list[dict]:
    """Read new shared works and legacy facility-specific records.

    Records are not assumed equivalent without a verified mapping.
    No data is modified.
    """
    exists = connection.execute(
        """
        SELECT 1 FROM managed_facilities
        WHERE facility_id = ?
        """,
        (facility_id,),
    ).fetchone()

    if exists is None:
        raise ValueError(f"Facility not found: {facility_id}")

    shared_rows = connection.execute(
        """
        SELECT
            w.construction_work_id,
            w.construction_name,
            w.status_code,
            w.planned_start_on,
            w.actual_completed_on,
            w.current_contract_amount_yen,
            w.final_amount_yen
        FROM construction_work_facilities AS wf
        JOIN construction_works AS w
          ON w.construction_work_id = wf.construction_work_id
        WHERE wf.facility_id = ?
        ORDER BY w.construction_work_id
        """,
        (facility_id,),
    ).fetchall()

    legacy_rows = connection.execute(
        """
        SELECT
            construction_history_id,
            project_name,
            started_on,
            completed_on,
            contract_amount_yen,
            final_amount_yen
        FROM facility_construction_history
        WHERE facility_id = ?
        ORDER BY construction_history_id
        """,
        (facility_id,),
    ).fetchall()

    result = [
        {
            "source": "SHARED",
            "record_id": row[0],
            "construction_name": row[1],
            "status_code": row[2],
            "started_on": row[3],
            "completed_on": row[4],
            "contract_amount_yen": row[5],
            "final_amount_yen": row[6],
        }
        for row in shared_rows
    ]

    result.extend(
        {
            "source": "LEGACY",
            "record_id": row[0],
            "construction_name": row[1],
            "status_code": None,
            "started_on": row[2],
            "completed_on": row[3],
            "contract_amount_yen": row[4],
            "final_amount_yen": row[5],
        }
        for row in legacy_rows
    )

    return result


def get_construction_cost_summary(
    connection: sqlite3.Connection,
    facility_ids: Iterable[int],
) -> dict:
    """Sum shared-work contract amounts once per distinct work.

    Legacy values are excluded pending verified migration/matching.
    Works with unknown amounts are counted separately.
    """
    facilities = _unique_ids(facility_ids, "facility_ids")
    if not facilities:
        return {
            "work_count": 0,
            "known_amount_count": 0,
            "unknown_amount_count": 0,
            "total_contract_amount_yen": 0,
        }

    placeholders = ", ".join("?" for _ in facilities)

    rows = connection.execute(
        """
        SELECT
            w.construction_work_id,
            w.current_contract_amount_yen
        FROM construction_works AS w
        WHERE w.construction_work_id IN (
            SELECT DISTINCT wf.construction_work_id
            FROM construction_work_facilities AS wf
            WHERE wf.facility_id IN (
        """
        + placeholders
        + """
            )
        )
        """,
        facilities,
    ).fetchall()

    amounts = [row[1] for row in rows]
    known = [value for value in amounts if value is not None]

    return {
        "work_count": len(rows),
        "known_amount_count": len(known),
        "unknown_amount_count": len(amounts) - len(known),
        "total_contract_amount_yen": sum(known),
    }
