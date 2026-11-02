
"""CSV exports for municipal budget and construction records.

Requires migrations 0010-0014 and the existing budget
execution/dashboard services.

All exports are read-only.
CSV bytes use UTF-8 with BOM for Japanese Excel compatibility.
"""

import csv
import io
from pathlib import Path

from road_river_management.services.budget_dashboard import (
    get_integrated_fiscal_dashboard,
)
from road_river_management.services.budget_execution import (
    get_payment_year_report,
)

BUDGET_COLUMNS = [
    "対象予算年度",
    "事業ID",
    "予算要求ID",
    "事業名",
    "予算要求名",
    "補正後成立予算額_円",
    "契約配分額_円",
    "全年度配分支払累計額_円",
    "予算契約配分差額_参考_円",
    "契約配分未払差額_参考_円",
]

CONTRACT_COLUMNS = [
    "対象予算年度",
    "事業ID",
    "予算要求ID",
    "工事契約ID",
    "工事名称",
    "契約状況",
    "契約日",
    "契約配分額_円",
    "配分理由",
]

PAYMENT_COLUMNS = [
    "支払年度",
    "支払ID",
    "工事契約ID",
    "工事名称",
    "支払伝票番号",
    "支払日",
    "支払額_円",
    "予算配分済支払額_円",
    "未配分支払額_円",
    "支払備考",
]


def _year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")

    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year is out of range")

    return value


def _project(value: int | None) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer")

    if value <= 0:
        raise ValueError("project_id must be positive")

    return value


def _safe_cell(value: object) -> object:
    """Prevent spreadsheet formula interpretation of text fields."""

    if value is None:
        return ""

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, (int, float)):
        return value

    text = str(value)

    # A spreadsheet may ignore leading whitespace when parsing
    # formulas. Protect text that becomes formula-like after
    # whitespace removal.
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text

    return text


def _encode_csv(
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(
        buffer,
        lineterminator="\r\n",
    )

    writer.writerow(columns)

    for row in rows:
        if len(row) != len(columns):
            raise ValueError("CSV column count mismatch")

        writer.writerow(
            [_safe_cell(value) for value in row]
        )

    return buffer.getvalue().encode("utf-8-sig")


def export_fiscal_budget_csv(
    connection,
    fiscal_year: int,
    *,
    project_id: int | None = None,
) -> bytes:
    """Export budget-year figures, one row per budget request."""

    year = _year(fiscal_year)
    selected_project = _project(project_id)

    report = get_integrated_fiscal_dashboard(
        connection,
        year,
        project_id=selected_project,
    )

    project_names = {
        project["project_id"]: project["project_name"]
        for project in report["projects"]
    }

    rows = []

    for item in report["requests"]:
        appropriation = item["effective_appropriation_yen"]
        contract = item["allocated_contract_yen"]
        paid = item[
            "allocated_payment_yen_all_years"
        ]

        rows.append(
            [
                year,
                item["project_id"],
                item["budget_request_id"],
                project_names[item["project_id"]],
                item["request_title"],
                appropriation,
                contract,
                paid,
                appropriation - contract,
                contract - paid,
            ]
        )

    return _encode_csv(BUDGET_COLUMNS, rows)


def export_contract_allocation_csv(
    connection,
    fiscal_year: int,
    *,
    project_id: int | None = None,
) -> bytes:
    """Export budget allocations, not duplicated contract totals."""

    year = _year(fiscal_year)
    selected_project = _project(project_id)

    if selected_project is not None:
        exists = connection.execute(
            """
            SELECT 1 FROM maintenance_projects
            WHERE project_id = ?
            """,
            (selected_project,),
        ).fetchone()

        if exists is None:
            raise ValueError("Project not found")

    records = connection.execute(
        """
        SELECT
            r.fiscal_year,
            r.project_id,
            r.budget_request_id,
            w.construction_work_id,
            w.construction_name,
            w.status_code,
            w.contract_date,
            a.allocated_contract_amount_yen,
            a.allocation_reason
        FROM budget_contract_allocations AS a
        JOIN budget_requests AS r
          ON r.budget_request_id = a.budget_request_id
        JOIN construction_works AS w
          ON w.construction_work_id = a.construction_work_id
        WHERE r.fiscal_year = ?
          AND (? IS NULL OR r.project_id = ?)
        ORDER BY
            r.project_id,
            w.construction_work_id,
            r.budget_request_id
        """,
        (year, selected_project, selected_project),
    ).fetchall()

    return _encode_csv(
        CONTRACT_COLUMNS,
        [list(record) for record in records],
    )


def export_payment_year_csv(
    connection,
    fiscal_year: int,
) -> bytes:
    """Export actual payment records exactly once each.

    This report is based on payment fiscal year.
    Budget allocations are pre-aggregated per payment.
    """

    year = _year(fiscal_year)

    records = connection.execute(
        """
        SELECT
            p.payment_fiscal_year,
            p.payment_id,
            w.construction_work_id,
            w.construction_name,
            p.payment_reference,
            p.paid_on,
            p.paid_amount_yen,
            COALESCE(a.allocated_yen, 0),
            p.paid_amount_yen -
                COALESCE(a.allocated_yen, 0),
            p.payment_note
        FROM construction_payments AS p
        JOIN construction_works AS w
          ON w.construction_work_id = p.construction_work_id
        LEFT JOIN (
            SELECT
                payment_id,
                SUM(allocated_payment_amount_yen)
                    AS allocated_yen
            FROM budget_payment_allocations
            GROUP BY payment_id
        ) AS a
          ON a.payment_id = p.payment_id
        WHERE p.payment_fiscal_year = ?
        ORDER BY p.paid_on, p.payment_id
        """,
        (year,),
    ).fetchall()

    return _encode_csv(
        PAYMENT_COLUMNS,
        [list(record) for record in records],
    )


def get_payment_export_summary(
    connection,
    fiscal_year: int,
) -> dict:
    """Return control totals for payment CSV reconciliation."""

    return get_payment_year_report(
        connection,
        _year(fiscal_year),
    )


def write_csv(
    destination: str | Path,
    data: bytes,
    *,
    overwrite: bool = False,
) -> Path:
    """Save generated CSV bytes to a specified local path.

    Directories must already exist.
    Existing files are protected unless overwrite=True.
    """

    path = Path(destination)

    if path.suffix.lower() != ".csv":
        raise ValueError("Destination must have a .csv extension")

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    mode = "wb" if overwrite else "xb"

    with path.open(mode) as file:
        file.write(data)

    return path
