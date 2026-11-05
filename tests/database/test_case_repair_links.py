"""Case/repair integration tests using the complete migration stack."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.case_management import create_case
from road_river_management.services.case_repair_links import (
    create_repair_for_case,
    link_repair_to_case,
    list_case_repairs,
    list_repair_cases,
    unlink_repair_from_case,
)


@pytest.fixture
def seeded(tmp_path):
    con = migrate.get_connection(tmp_path / "case_repair.sqlite")
    migrate.ensure_schema_versions(con)
    migrate.upgrade(con)
    uid = con.execute(
        """INSERT INTO users
           (user_uuid, login_id, display_name, password_hash, password_algorithm)
           VALUES (?, 'case-repair-tester', 'テスト職員', 'test', 'TEST')""",
        (str(uuid4()),),
    ).lastrowid
    type_id = con.execute(
        "SELECT facility_type_id FROM facility_types WHERE type_code='BRIDGE'"
    ).fetchone()[0]
    status_id = con.execute(
        "SELECT facility_status_id FROM facility_statuses WHERE status_code='ACTIVE'"
    ).fetchone()[0]
    fid = con.execute(
        """INSERT INTO managed_facilities
           (facility_uuid, facility_name, facility_type_id, facility_status_id)
           VALUES (?, 'テスト橋梁', ?, ?)""",
        (str(uuid4()), type_id, status_id),
    ).lastrowid
    cids = [
        create_case(
            con,
            case_type_code="COMPLAINT_REQUEST",
            title=f"橋梁についての要望{i}",
            received_on="2027-04-01",
            created_by_user_id=uid,
        )
        for i in range(2)
    ]
    rids = [
        con.execute(
            """INSERT INTO facility_repairs
               (repair_uuid, facility_id, repair_name, repair_type)
               VALUES (?, ?, ?, 'PLANNED')""",
            (str(uuid4()), fid, f"高欄補修{i}"),
        ).lastrowid
        for i in range(2)
    ]
    con.commit()
    yield con, uid, fid, cids, rids
    con.close()


def test_link_propagates_facility_and_reads_both_directions(seeded):
    con, uid, fid, cids, rids = seeded
    link_repair_to_case(con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid)
    assert con.execute(
        """SELECT 1 FROM administrative_case_facilities
           WHERE case_id=? AND facility_id=?""",
        (cids[0], fid),
    ).fetchone()
    assert list_case_repairs(con, cids[0])[0]["repair_id"] == rids[0]
    assert list_repair_cases(con, rids[0])[0]["case_id"] == cids[0]


def test_many_to_many_without_duplicate_repairs(seeded):
    con, uid, _, cids, rids = seeded
    for cid in cids:
        for rid in rids:
            link_repair_to_case(con, case_id=cid, repair_id=rid, actor_user_id=uid)
    assert len(list_case_repairs(con, cids[0])) == 2
    assert len(list_repair_cases(con, rids[0])) == 2
    assert con.execute("SELECT COUNT(*) FROM facility_repairs").fetchone()[0] == 2


def test_duplicate_link_is_atomic(seeded):
    con, uid, _, cids, rids = seeded
    link_repair_to_case(con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid)
    with pytest.raises(sqlite3.IntegrityError):
        link_repair_to_case(con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid)
    assert con.execute("SELECT COUNT(*) FROM case_repair_link_events").fetchone()[0] == 1


def test_create_repair_from_case_once(seeded):
    con, uid, fid, cids, _ = seeded
    result = create_repair_for_case(
        con,
        case_id=cids[0],
        facility_id=fid,
        repair_name="橋面補修",
        repair_type="EMERGENCY",
        actor_user_id=uid,
    )
    assert len(list_case_repairs(con, cids[0])) == 1
    assert result["repair_id"] == list_case_repairs(con, cids[0])[0]["repair_id"]
    assert con.execute(
        "SELECT COUNT(*) FROM facility_repairs WHERE repair_id=?", (result["repair_id"],)
    ).fetchone()[0] == 1


def test_invalid_create_does_not_write(seeded):
    con, uid, fid, cids, _ = seeded
    before = con.total_changes
    with pytest.raises(ValueError, match="Invalid repair_type"):
        create_repair_for_case(
            con, case_id=cids[0], facility_id=fid, repair_name="補修",
            repair_type="INVALID", actor_user_id=uid,
        )
    assert con.total_changes == before


def test_unlink_retains_history_and_allows_relink(seeded):
    con, uid, fid, cids, rids = seeded
    original = link_repair_to_case(
        con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid,
    )
    unlink_repair_from_case(
        con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid,
        reason="誤関連付けを訂正",
    )
    assert list_case_repairs(con, cids[0]) == []
    assert con.execute(
        "SELECT COUNT(*) FROM administrative_case_facilities WHERE case_id=? AND facility_id=?",
        (cids[0], fid),
    ).fetchone()[0] == 1
    newer = link_repair_to_case(
        con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid,
    )
    assert original != newer
    assert con.execute("SELECT COUNT(*) FROM case_repair_link_events").fetchone()[0] == 3


def test_immutable_events(seeded):
    con, uid, _, cids, rids = seeded
    link_repair_to_case(con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM case_repair_link_events")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE case_repair_link_events SET event_note='bad'")


def test_read_only_functions_do_not_write(seeded):
    con, uid, _, cids, rids = seeded
    link_repair_to_case(con, case_id=cids[0], repair_id=rids[0], actor_user_id=uid)
    before = con.total_changes
    list_case_repairs(con, cids[0])
    list_repair_cases(con, rids[0])
    assert con.total_changes == before
