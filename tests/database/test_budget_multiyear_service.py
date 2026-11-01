
"""Tests for budget adjustment and carryover workflows."""

from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.budget_multiyear import (
    authorize_carryover,
    cancel_carryover,
    create_carryover,
    finalize_carryover,
    get_carryover_summary,
    get_effective_appropriation,
    record_appropriation_adjustment,
)


@pytest.fixture
def db(tmp_path):
    connection = migrate.get_connection(
        tmp_path / "budget_multiyear_service.sqlite"
    )
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    yield connection
    connection.close()


@pytest.fixture
def budget(db):
    project_id = db.execute(
        """
        INSERT INTO maintenance_projects (
            project_uuid, project_name
        )
        VALUES (?, '架空橋梁修繕')
        """,
        (str(uuid4()),),
    ).lastrowid

    request_id = db.execute(
        """
        INSERT INTO budget_requests (
            request_uuid, project_id, fiscal_year,
            request_title,
            original_requested_amount_yen,
            current_requested_amount_yen
        )
        VALUES (?, ?, 2027, '橋梁修繕予算',
                30000000, 30000000)
        """,
        (str(uuid4()), project_id),
    ).lastrowid

    appropriation_id = db.execute(
        """
        INSERT INTO budget_appropriations (
            budget_request_id,
            appropriation_type_code,
            appropriated_amount_yen,
            approved_on
        )
        VALUES (?, 'ORIGINAL', 20000000, '2027-03-20')
        """,
        (request_id,),
    ).lastrowid

    db.commit()

    return request_id, appropriation_id


def adjust(db, appropriation_id, delta):
    return record_appropriation_adjustment(
        db,
        appropriation_id,
        adjustment_type_code=(
            "INCREASE" if delta > 0 else "DECREASE"
        ),
        amount_delta_yen=delta,
        approved_on="2027-09-20",
        adjustment_reason="補正予算による変更",
    )


def draft(db, request_id, kind="MEIKYO"):
    arguments = {}
    if kind == "ACCIDENT":
        arguments = {
            "unavoidable_accident_description": (
                "施工中に避け難い障害が発生"
            ),
            "obligation_incurred_on": "2027-10-01",
        }

    return create_carryover(
        db,
        request_id,
        carryover_type_code=kind,
        carryover_reason="工事期間の変更",
        **arguments,
    )


def authorize(db, carryover_id, limit=10_000_000):
    authorize_carryover(
        db,
        carryover_id,
        authorized_limit_yen=limit,
        authorization_date="2028-03-20",
        decision_reference="架空決定第001号",
    )


def finalize(db, carryover_id, amount=8_000_000):
    finalize_carryover(
        db,
        carryover_id,
        actual_carryover_amount_yen=amount,
        finalization_date="2028-04-01",
    )


def test_adjustment_increases_effective_amount(db, budget):
    request_id, appropriation_id = budget

    adjust(db, appropriation_id, 5_000_000)

    summary = get_effective_appropriation(db, request_id)

    assert summary["recorded_appropriations_yen"] == 20_000_000
    assert summary["net_adjustments_yen"] == 5_000_000
    assert summary["effective_appropriation_yen"] == 25_000_000


def test_multiple_adjustments_keep_original_amount(db, budget):
    request_id, appropriation_id = budget

    adjust(db, appropriation_id, 5_000_000)
    adjust(db, appropriation_id, -3_000_000)

    summary = get_effective_appropriation(db, request_id)

    assert summary["recorded_appropriations_yen"] == 20_000_000
    assert summary["effective_appropriation_yen"] == 22_000_000

    numbers = db.execute(
        """
        SELECT adjustment_number
        FROM budget_appropriation_adjustments
        ORDER BY adjustment_number
        """
    ).fetchall()

    assert numbers == [(1,), (2,)]


def test_adjustment_cannot_make_total_negative(db, budget):
    request_id, appropriation_id = budget

    with pytest.raises(ValueError, match="negative"):
        adjust(db, appropriation_id, -21_000_000)

    assert get_effective_appropriation(
        db, request_id
    )["effective_appropriation_yen"] == 20_000_000

    assert not db.in_transaction


def test_invalid_adjustment_direction_rejected(db, budget):
    _, appropriation_id = budget

    with pytest.raises(ValueError, match="positive"):
        record_appropriation_adjustment(
            db,
            appropriation_id,
            adjustment_type_code="INCREASE",
            amount_delta_yen=-1,
            approved_on="2027-09-20",
            adjustment_reason="不正な変更",
        )


def test_meikyo_draft_has_correct_years(db, budget):
    request_id, _ = budget

    cid = draft(db, request_id)

    row = db.execute(
        """
        SELECT source_fiscal_year,
               destination_fiscal_year,
               carryover_status_code
        FROM budget_carryovers
        WHERE carryover_id = ?
        """,
        (cid,),
    ).fetchone()

    assert row == (2027, 2028, "DRAFT")


def test_accident_requires_additional_details(db, budget):
    request_id, _ = budget

    with pytest.raises((TypeError, ValueError)):
        create_carryover(
            db,
            request_id,
            carryover_type_code="ACCIDENT",
            carryover_reason="避け難い事故",
        )

    assert not db.in_transaction


def test_accident_with_details_can_be_drafted(db, budget):
    request_id, _ = budget

    cid = draft(db, request_id, "ACCIDENT")
    assert cid > 0


def test_carryover_authorization(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)

    authorize(db, cid)

    result = get_carryover_summary(db, request_id)

    assert result["carryover_count"] == 1
    assert result["carryovers"][0]["status_code"] == "AUTHORIZED"
    assert result["carryovers"][0][
        "authorized_limit_yen"
    ] == 10_000_000


def test_finalization_requires_authorization(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)

    with pytest.raises(ValueError, match="authorized"):
        finalize(db, cid)

    assert get_carryover_summary(
        db, request_id
    )["finalized_total_yen"] == 0


def test_carryover_full_workflow(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)

    authorize(db, cid)
    finalize(db, cid)

    summary = get_carryover_summary(db, request_id)

    assert summary["finalized_total_yen"] == 8_000_000
    assert summary["carryovers"][0]["status_code"] == "FINALIZED"


def test_carryover_cannot_exceed_limit(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)
    authorize(db, cid)

    with pytest.raises(ValueError, match="limit"):
        finalize(db, cid, 11_000_000)

    row = db.execute(
        """
        SELECT carryover_status_code
        FROM budget_carryovers
        WHERE carryover_id = ?
        """,
        (cid,),
    ).fetchone()

    assert row == ("AUTHORIZED",)
    assert not db.in_transaction


def test_multiple_carryovers_cannot_exceed_appropriation(
    db, budget
):
    request_id, _ = budget

    first = draft(db, request_id)
    authorize(db, first, 15_000_000)
    finalize(db, first, 15_000_000)

    second = draft(db, request_id)
    authorize(db, second, 10_000_000)

    with pytest.raises(ValueError, match="appropriation"):
        finalize(db, second, 6_000_000)

    assert get_carryover_summary(
        db, request_id
    )["finalized_total_yen"] == 15_000_000


def test_adjustment_cannot_undercut_finalized_carryover(
    db, budget
):
    request_id, appropriation_id = budget

    cid = draft(db, request_id)
    authorize(db, cid, 15_000_000)
    finalize(db, cid, 15_000_000)

    with pytest.raises(ValueError, match="finalized"):
        adjust(db, appropriation_id, -6_000_000)

    assert get_effective_appropriation(
        db, request_id
    )["effective_appropriation_yen"] == 20_000_000


def test_cancel_preserves_carryover_record(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)

    cancel_carryover(
        db, cid, cancellation_reason="事業予定の変更"
    )

    summary = get_carryover_summary(db, request_id)
    assert summary["carryover_count"] == 1
    assert summary["carryovers"][0]["status_code"] == "CANCELLED"


def test_finalized_carryover_cannot_be_cancelled(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)
    authorize(db, cid)
    finalize(db, cid)

    with pytest.raises(ValueError, match="cannot be cancelled"):
        cancel_carryover(
            db, cid, cancellation_reason="不適切な取消し"
        )


def test_history_event_is_written(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)
    authorize(db, cid)
    finalize(db, cid)

    descriptions = db.execute(
        """
        SELECT event_description
        FROM budget_request_events
        WHERE budget_request_id = ?
        ORDER BY budget_event_id
        """,
        (request_id,),
    ).fetchall()

    assert len(descriptions) == 3
    assert "draft" in descriptions[0][0]
    assert "authorized" in descriptions[1][0]
    assert "finalized" in descriptions[2][0]


def test_database_integrity(db, budget):
    request_id, _ = budget
    cid = draft(db, request_id)
    authorize(db, cid)
    finalize(db, cid)

    assert db.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0] == "ok"

    assert db.execute(
        "PRAGMA foreign_key_check"
    ).fetchall() == []
