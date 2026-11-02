
"""Read-only carryover report data and CSV exports.

Requires migrations 0011-0012 and carryover_checklist.py.

Outputs are working documents, not official statutory forms.
"""

import csv
import io
import sqlite3
from collections.abc import Collection

from road_river_management.services.carryover_checklist import (
    get_carryover_checklist,
)

CARRYOVER_TYPES = {
    "MEIKYO": "繰越明許費",
    "ACCIDENT": "事故繰越し",
}

STATUSES = {
    "DRAFT": "起案中",
    "AUTHORIZED": "承認記録済",
    "FINALIZED": "繰越額確定",
    "CANCELLED": "取消",
}

LIST_COLUMNS = [
    "繰越ID",
    "事業ID",
    "事業名",
    "予算要求ID",
    "予算要求名",
    "繰越区分",
    "処理状況",
    "繰越元年度",
    "繰越先年度",
    "承認限度額_円",
    "確定繰越額_円",
    "繰越理由",
    "決定文書番号",
]

DETAIL_COLUMNS = ["項目", "内容"]

CHECK_COLUMNS = [
    "分類",
    "コード",
    "確認内容",
    "状況",
]


def _id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _year(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fiscal_year must be an integer")
    if not 1900 <= value <= 2200:
        raise ValueError("fiscal_year out of range")
    return value


def _safe_cell(value: object) -> object:
    """Escape formula-like CSV text for spreadsheet applications."""

    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(
        value, bool
    ):
        return value

    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _csv_bytes(columns: list[str], rows: list[list]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)

    for row in rows:
        if len(row) != len(columns):
            raise ValueError("CSV column count mismatch")
        writer.writerow([_safe_cell(v) for v in row])

    return output.getvalue().encode("utf-8-sig")


def _rows(
    connection: sqlite3.Connection,
    *,
    fiscal_year: int | None = None,
    carryover_type_code: str | None = None,
    project_id: int | None = None,
) -> list[dict]:
    if fiscal_year is not None:
        _year(fiscal_year)
    if project_id is not None:
        _id(project_id, "project_id")
    if carryover_type_code is not None and carryover_type_code not in CARRYOVER_TYPES:
        raise ValueError("Invalid carryover type")

    cursor = connection.execute(
        """
        SELECT
            c.carryover_id,
            p.project_id,
            p.project_name,
            r.budget_request_id,
            r.request_title,
            c.carryover_type_code,
            c.carryover_status_code,
            c.source_fiscal_year,
            c.destination_fiscal_year,
            c.authorized_limit_yen,
            c.actual_carryover_amount_yen,
            c.carryover_reason,
            c.decision_reference,
            c.unavoidable_accident_description,
            c.obligation_incurred_on,
            c.authorization_date,
            c.finalization_date
        FROM budget_carryovers AS c
        JOIN budget_requests AS r
          ON r.budget_request_id = c.budget_request_id
        JOIN maintenance_projects AS p
          ON p.project_id = r.project_id
        WHERE (? IS NULL OR c.source_fiscal_year = ?)
          AND (? IS NULL OR c.carryover_type_code = ?)
          AND (? IS NULL OR p.project_id = ?)
        ORDER BY
            c.source_fiscal_year,
            p.project_id,
            c.carryover_id
        """,
        (
            fiscal_year,
            fiscal_year,
            carryover_type_code,
            carryover_type_code,
            project_id,
            project_id,
        ),
    )

    keys = [description[0] for description in cursor.description]
    return [dict(zip(keys, row, strict=True)) for row in cursor]


def get_carryover_reason_report(
    connection: sqlite3.Connection,
    carryover_id: int,
) -> dict:
    """Return source fields for a draft carryover reason report."""

    cid = _id(carryover_id, "carryover_id")
    matching = [
        item for item in _rows(connection)
        if item["carryover_id"] == cid
    ]
    if not matching:
        raise ValueError("Carryover not found")

    item = matching[0]
    return {
        **item,
        "carryover_type_name": CARRYOVER_TYPES[
            item["carryover_type_code"]
        ],
        "status_name": STATUSES[
            item["carryover_status_code"]
        ],
        "document_status": "確認用ドラフト",
        "official_document": False,
    }


def get_carryover_list_report(
    connection: sqlite3.Connection,
    fiscal_year: int,
    *,
    carryover_type_code: str | None = None,
    project_id: int | None = None,
    include_cancelled: bool = False,
) -> dict:
    """List carryovers by source year, with no monetary duplication."""

    year = _year(fiscal_year)
    items = _rows(
        connection,
        fiscal_year=year,
        carryover_type_code=carryover_type_code,
        project_id=project_id,
    )

    if not include_cancelled:
        items = [
            item for item in items
            if item["carryover_status_code"] != "CANCELLED"
        ]

    finalized = [
        item for item in items
        if item["carryover_status_code"] == "FINALIZED"
    ]

    return {
        "source_fiscal_year": year,
        "carryover_type_code": carryover_type_code,
        "project_id": project_id,
        "record_count": len(items),
        "finalized_count": len(finalized),
        "finalized_total_yen": sum(
            item["actual_carryover_amount_yen"]
            for item in finalized
        ),
        "records": items,
        "official_document": False,
    }


def get_carryover_review_report(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    reason_code: str,
    evidence_codes: Collection[str] | None = None,
) -> dict:
    """Combine the reason report with the existing checklist."""

    reason = get_carryover_reason_report(
        connection, carryover_id
    )
    checklist = get_carryover_checklist(
        connection,
        carryover_id,
        reason_code=reason_code,
        evidence_codes=evidence_codes,
    )

    return {
        "reason_report": reason,
        "checklist": checklist,
        "official_document": False,
    }


def export_carryover_reason_csv(
    connection: sqlite3.Connection,
    carryover_id: int,
) -> bytes:
    """Export one draft reason sheet as label/value rows."""

    data = get_carryover_reason_report(
        connection, carryover_id
    )

    fields = [
        ("帳票区分", "繰越理由書（確認用ドラフト）"),
        ("事業名", data["project_name"]),
        ("予算要求名", data["request_title"]),
        ("繰越区分", data["carryover_type_name"]),
        ("処理状況", data["status_name"]),
        ("繰越元年度", data["source_fiscal_year"]),
        ("繰越先年度", data["destination_fiscal_year"]),
        ("繰越理由", data["carryover_reason"]),
        ("承認限度額_円", data["authorized_limit_yen"]),
        ("確定繰越額_円", data["actual_carryover_amount_yen"]),
        ("支出負担行為日", data["obligation_incurred_on"]),
        (
            "避け難い事故の説明",
            data["unavoidable_accident_description"],
        ),
        ("承認日", data["authorization_date"]),
        ("確定日", data["finalization_date"]),
        ("決定文書番号", data["decision_reference"]),
    ]

    return _csv_bytes(
        DETAIL_COLUMNS,
        [[label, value] for label, value in fields],
    )


def export_carryover_list_csv(
    connection: sqlite3.Connection,
    fiscal_year: int,
    *,
    carryover_type_code: str | None = None,
    project_id: int | None = None,
    include_cancelled: bool = False,
) -> bytes:
    """Export one row per carryover record."""

    report = get_carryover_list_report(
        connection,
        fiscal_year,
        carryover_type_code=carryover_type_code,
        project_id=project_id,
        include_cancelled=include_cancelled,
    )

    rows = []
    for item in report["records"]:
        rows.append([
            item["carryover_id"],
            item["project_id"],
            item["project_name"],
            item["budget_request_id"],
            item["request_title"],
            CARRYOVER_TYPES[item["carryover_type_code"]],
            STATUSES[item["carryover_status_code"]],
            item["source_fiscal_year"],
            item["destination_fiscal_year"],
            item["authorized_limit_yen"],
            item["actual_carryover_amount_yen"],
            item["carryover_reason"],
            item["decision_reference"],
        ])

    return _csv_bytes(LIST_COLUMNS, rows)


def export_carryover_review_csv(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    reason_code: str,
    evidence_codes: Collection[str] | None = None,
) -> bytes:
    """Export review checklist; declarations are not approvals."""

    report = get_carryover_review_report(
        connection,
        carryover_id,
        reason_code=reason_code,
        evidence_codes=evidence_codes,
    )

    checklist = report["checklist"]

    rows = [
        [
            "基本情報",
            "CARRYOVER",
            "繰越確認票（審査支援用）",
            "未承認",
        ],
        [
            "基本情報",
            "REASON",
            checklist["reason_label"],
            "確認対象",
        ],
    ]

    for document in checklist["required_documents"]:
        rows.append([
            "必要資料",
            document["code"],
            document["label"],
            (
                "あり（自己申告）"
                if document["declared_present"]
                else "未申告"
            ),
        ])

    for code in checklist["missing_field_codes"]:
        rows.append([
            "不足項目",
            code,
            code,
            "要補完",
        ])

    for code in checklist["unknown_evidence_codes"]:
        rows.append([
            "未認識資料",
            code,
            code,
            "要確認",
        ])

    for number, review in enumerate(
        checklist["manual_review_items"], start=1
    ):
        rows.append([
            "担当者確認",
            f"REVIEW_{number}",
            review,
            "未確認",
        ])

    for number, action in enumerate(
        checklist["next_actions"], start=1
    ):
        rows.append([
            "次の作業",
            f"ACTION_{number}",
            action,
            "未対応",
        ])

    return _csv_bytes(CHECK_COLUMNS, rows)
