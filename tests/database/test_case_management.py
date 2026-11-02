"""Tests against complete existing migration stack."""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.services.case_management import (
    add_response,
    change_case_status,
    create_case,
    get_case,
    list_active_cases,
    set_followup,
)


@pytest.fixture
def db(tmp_path):
    con = migrate.get_connection(tmp_path / 'cases.sqlite')
    migrate.ensure_schema_versions(con)
    migrate.upgrade(con)
    user = con.execute(
        """INSERT INTO users (user_uuid,login_id,display_name,password_hash,password_algorithm)
           VALUES (?, 'case-worker', '担当者', 'testing-hash', 'TEST')""",
        (str(uuid4()),),
    ).lastrowid
    con.commit()
    yield con, user
    con.close()


def _new(db, **kwargs):
    con, user = db
    return create_case(con, case_type_code='CONSULTATION', title='道路相談',
                       received_on='2027-04-01', created_by_user_id=user, **kwargs)


def test_migration_tables_and_initial_state(db):
    con, _ = db
    cid = _new(db)
    result = get_case(con, cid)
    assert result['status_code'] == 'OPEN'
    assert result['closed_on'] is None
    assert result['events'][0]['event_type_code'] == 'CREATED'


def test_three_categories(db):
    con, user = db
    for kind in ('CONSULTATION', 'COMPLAINT_REQUEST', 'CIVIL_WORKS_REQUEST'):
        cid = create_case(con, case_type_code=kind, title='案件', received_on='2027-04-01',
                          created_by_user_id=user)
        assert get_case(con, cid)['case_type_code'] == kind


def test_response_history_append_only(db):
    con, user = db
    cid = _new(db)
    rid = add_response(con, cid, response_on='2027-04-02', response_type_code='SITE_VISIT',
                       content='現地を確認', recorded_by_user_id=user)
    assert get_case(con, cid)['responses'][0]['content'] == '現地を確認'
    with pytest.raises(sqlite3.IntegrityError):
        con.execute('DELETE FROM administrative_case_responses WHERE response_id=?', (rid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute('UPDATE administrative_case_responses SET content=? WHERE response_id=?',
                    ('改ざん', rid))


def test_followup_and_overdue_queue(db):
    con, user = db
    cid = _new(db, due_on='2027-04-03')
    set_followup(con, cid, changed_by_user_id=user, followup_on='2027-04-02',
                 followup_reason='再確認')
    report = list_active_cases(con, as_of='2027-04-04')
    assert report['active_count'] == 1
    assert report['overdue_count'] == 1
    assert report['followup_due_count'] == 1


def test_close_excludes_queue_and_clears_followup(db):
    con, user = db
    cid = _new(db)
    set_followup(con, cid, changed_by_user_id=user, followup_on='2027-04-10',
                 followup_reason='点検予定')
    change_case_status(con, cid, status_code='CLOSED', changed_by_user_id=user,
                       changed_on='2027-04-09')
    result = get_case(con, cid)
    assert result['closed_on'] == '2027-04-09'
    assert result['followup_required'] == 0
    assert list_active_cases(con, as_of='2027-04-10')['active_count'] == 0
    assert len(result['events']) == 3


def test_reopen(db):
    con, user = db
    cid = _new(db)
    change_case_status(con, cid, status_code='CLOSED', changed_by_user_id=user,
                       changed_on='2027-04-09')
    change_case_status(con, cid, status_code='IN_PROGRESS', changed_by_user_id=user,
                       changed_on='2027-04-10')
    assert get_case(con, cid)['closed_on'] is None


def test_invalid_input_does_not_insert(db):
    con, user = db
    with pytest.raises(ValueError):
        create_case(con, case_type_code='INVALID', title='案件', received_on='2027-04-01',
                    created_by_user_id=user)
    with pytest.raises(ValueError):
        create_case(con, case_type_code='CONSULTATION', title='案件',
                    received_on='2027-02-30', created_by_user_id=user)
    assert con.execute('SELECT COUNT(*) FROM administrative_cases').fetchone()[0] == 0


def test_invalid_facility_rolls_back_case_and_event(db):
    con, _ = db
    with pytest.raises(sqlite3.IntegrityError):
        _new(db, facility_ids=(999999,))
    assert con.execute('SELECT COUNT(*) FROM administrative_cases').fetchone()[0] == 0
    assert con.execute('SELECT COUNT(*) FROM administrative_case_events').fetchone()[0] == 0


def test_case_linked_to_facility(db):
    con, _ = db
    type_id = con.execute("SELECT facility_type_id FROM facility_types WHERE type_code='ROAD'").fetchone()[0]
    status_id = con.execute("SELECT facility_status_id FROM facility_statuses WHERE status_code='ACTIVE'").fetchone()[0]
    fid = con.execute('''INSERT INTO managed_facilities
       (facility_uuid,facility_name,facility_type_id,facility_status_id)
       VALUES (?,?,?,?)''', (str(uuid4()), 'テスト道路', type_id, status_id)).lastrowid
    cid = _new(db, facility_ids=(fid,))
    assert get_case(con, cid)['facility_ids'] == [fid]


def test_read_report_does_not_write(db):
    con, _ = db
    cid = _new(db)
    con.commit()
    before = con.total_changes
    get_case(con, cid)
    list_active_cases(con, as_of='2027-04-02')
    assert con.total_changes == before


def test_cannot_followup_closed_case(db):
    con, user = db
    cid = _new(db)
    change_case_status(con, cid, status_code='CLOSED', changed_by_user_id=user,
                       changed_on='2027-04-09')
    with pytest.raises(ValueError):
        set_followup(con, cid, changed_by_user_id=user,
                     followup_on='2027-04-10', followup_reason='不可')
