
"""Reason-specific carryover workflow checklist.

Requires migration 0012.
Read-only: does not approve carryovers or verify legal eligibility.

Evidence codes are caller-supplied declarations, not proof that
a document exists or has been reviewed.
"""

import sqlite3
from collections.abc import Collection

REASONS = {
    "LAND": {
        "label": "用地取得・補償交渉",
        "evidence": {
            "LAND_NEGOTIATION": "用地・補償交渉の経過資料",
        },
        "review": (
            "用地交渉と工程遅延の因果関係を確認",
        ),
    },
    "CONSULTATION": {
        "label": "関係機関協議",
        "evidence": {
            "CONSULTATION_RECORD": "関係機関との協議記録",
        },
        "review": (
            "協議の開始日・経過と遅延理由を確認",
        ),
    },
    "DESIGN": {
        "label": "設計・施工条件変更",
        "evidence": {
            "DESIGN_CHANGE": "設計・施工条件の変更資料",
        },
        "review": (
            "変更の必要性と工程への影響を確認",
        ),
    },
    "PROCUREMENT": {
        "label": "入札・資材調達",
        "evidence": {
            "PROCUREMENT_RECORD": "入札・調達の経過資料",
        },
        "review": (
            "調達遅延の原因と代替策を確認",
        ),
    },
    "DISASTER": {
        "label": "災害・異常気象",
        "evidence": {
            "DISASTER_RECORD": "災害・気象等の影響記録",
        },
        "review": (
            "発生日時・場所・事業への影響を確認",
        ),
    },
    "OTHER": {
        "label": "その他",
        "evidence": {
            "OTHER_EXPLANATION": "具体的な原因を説明する資料",
        },
        "review": (
            "繰越理由の具体性と妥当性を確認",
        ),
    },
}

COMMON_EVIDENCE = {
    "REVISED_SCHEDULE": "変更後の工程表",
    "AMOUNT_BREAKDOWN": "繰越対象額の内訳資料",
}

ACCIDENT_EVIDENCE = {
    "OBLIGATION_RECORD": "支出負担行為を確認できる記録",
    "ACCIDENT_RECORD": "避け難い事故の発生・影響資料",
}

COMMON_REVIEWS = (
    "年度内に支出を終えられない事情を確認",
    "繰越額と財源・執行状況を財政担当部署と照合",
    "自治体の決裁規程・提出期限・様式を確認",
)

ACCIDENT_REVIEWS = (
    "支出負担行為の時期と内容を確認",
    "事故発生時期と支出負担行為の前後関係を確認",
    "事故が避け難いものかを審査担当者が確認",
    "繰越明許費との適用関係を確認",
)

MEIKYO_REVIEWS = (
    "繰越明許費の予算上の定めを確認",
    "予算成立後の事情または経費の性質を確認",
    "繰越計算書の作成・議会報告手続きを確認",
)


def _positive_id(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("reason_code must be a string")
    if value not in REASONS:
        raise ValueError(f"Unknown carryover reason: {value}")
    return value


def _evidence_codes(
    values: Collection[str] | None,
) -> set[str]:
    if values is None:
        return set()

    if isinstance(values, (str, bytes)) or not isinstance(
        values, (set, list, tuple, frozenset)
    ):
        raise TypeError("evidence_codes must be a collection of codes")

    if any(not isinstance(value, str) for value in values):
        raise TypeError("Each evidence code must be a string")

    return set(values)


def list_carryover_reason_options() -> list[dict]:
    """Return available reason categories for a selection control."""

    return [
        {"code": code, "label": details["label"]}
        for code, details in REASONS.items()
    ]


def get_carryover_checklist(
    connection: sqlite3.Connection,
    carryover_id: int,
    *,
    reason_code: str,
    evidence_codes: Collection[str] | None = None,
) -> dict:
    """Create a non-binding checklist for an existing carryover.

    evidence_codes represents declarations made by the caller.
    They are not proof of file attachment, validity or approval.
    """

    cid = _positive_id(carryover_id, "carryover_id")
    reason = _reason(reason_code)
    provided = _evidence_codes(evidence_codes)

    row = connection.execute(
        """
        SELECT
            c.carryover_id,
            c.budget_request_id,
            c.carryover_type_code,
            c.carryover_status_code,
            c.source_fiscal_year,
            c.destination_fiscal_year,
            c.carryover_reason,
            c.unavoidable_accident_description,
            c.obligation_incurred_on,
            c.authorized_limit_yen,
            c.actual_carryover_amount_yen,
            c.authorization_date,
            c.finalization_date,
            c.decision_reference,
            r.project_id,
            r.request_title
        FROM budget_carryovers AS c
        JOIN budget_requests AS r
          ON r.budget_request_id = c.budget_request_id
        WHERE c.carryover_id = ?
        """,
        (cid,),
    ).fetchone()

    if row is None:
        raise ValueError("Carryover not found")

    (
        record_id,
        request_id,
        carryover_type,
        status,
        source_year,
        destination_year,
        reason_text,
        accident_description,
        obligation_date,
        authorized_limit,
        actual_amount,
        authorization_date,
        finalization_date,
        decision_reference,
        project_id,
        request_title,
    ) = row

    required_documents = {
        **COMMON_EVIDENCE,
        **REASONS[reason]["evidence"],
    }

    reviews = [
        *COMMON_REVIEWS,
        *REASONS[reason]["review"],
    ]

    missing_fields = []

    if not reason_text or not reason_text.strip():
        missing_fields.append("carryover_reason")

    if carryover_type == "ACCIDENT":
        required_documents.update(ACCIDENT_EVIDENCE)
        reviews.extend(ACCIDENT_REVIEWS)
        if not accident_description or not accident_description.strip():
            missing_fields.append(
                "unavoidable_accident_description"
            )
        if not obligation_date:
            missing_fields.append("obligation_incurred_on")
    elif carryover_type == "MEIKYO":
        reviews.extend(MEIKYO_REVIEWS)
    else:
        raise ValueError("Unknown carryover type in database")

    if status in {"AUTHORIZED", "FINALIZED"}:
        if authorized_limit is None:
            missing_fields.append("authorized_limit_yen")
        if not authorization_date:
            missing_fields.append("authorization_date")
        if not decision_reference or not decision_reference.strip():
            missing_fields.append("decision_reference")

    if status == "FINALIZED":
        if actual_amount is None:
            missing_fields.append("actual_carryover_amount_yen")
        if not finalization_date:
            missing_fields.append("finalization_date")

    if (
        actual_amount is not None
        and authorized_limit is not None
        and actual_amount > authorized_limit
    ):
        missing_fields.append(
            "actual_amount_exceeds_authorized_limit"
        )

    documents = [
        {
            "code": code,
            "label": label,
            "declared_present": code in provided,
        }
        for code, label in required_documents.items()
    ]
    missing_documents = [
        item["code"]
        for item in documents
        if not item["declared_present"]
    ]
    unknown_evidence = sorted(provided - set(required_documents))

    next_actions = []
    if missing_fields:
        next_actions.append("不足している登録項目を補完する")
    if missing_documents:
        next_actions.append("未申告の必要資料を収集・登録する")
    if unknown_evidence:
        next_actions.append("認識できない資料コードを確認する")
    next_actions.append("確認事項を担当者・財政担当部署が審査する")

    return {
        "carryover_id": record_id,
        "budget_request_id": request_id,
        "project_id": project_id,
        "request_title": request_title,
        "carryover_type_code": carryover_type,
        "carryover_status_code": status,
        "source_fiscal_year": source_year,
        "destination_fiscal_year": destination_year,
        "reason_code": reason,
        "reason_label": REASONS[reason]["label"],
        "required_documents": documents,
        "missing_document_codes": missing_documents,
        "unknown_evidence_codes": unknown_evidence,
        "missing_field_codes": missing_fields,
        "manual_review_items": reviews,
        "next_actions": next_actions,
        "data_complete": (
            not missing_fields
            and not missing_documents
            and not unknown_evidence
        ),
        "legal_eligibility_confirmed": False,
        "approval_confirmed_by_checklist": False,
    }
