"""Request-level checks for case/repair UI, authorization, CSRF and atomic writes."""

import sqlite3
from contextlib import contextmanager
from uuid import uuid4

import pytest
from flask import Flask

from road_river_management.database import migrate
from road_river_management.services.case_management import create_case
from road_river_management.web.case_repair_ui import create_case_repair_ui_blueprint


@pytest.fixture
def fixture_data(tmp_path):
    path = tmp_path / "case_repair_ui.sqlite"
    con = migrate.get_connection(path)
    migrate.ensure_schema_versions(con)
    migrate.upgrade(con)
    uid = con.execute(
        """INSERT INTO users (user_uuid,login_id,display_name,password_hash,password_algorithm)
           VALUES (?, 'ui-user', 'テスト職員', 'unused', 'TEST')""", (str(uuid4()),)
    ).lastrowid
    type_id = con.execute(
        "SELECT facility_type_id FROM facility_types WHERE type_code='BRIDGE'"
    ).fetchone()[0]
    status_id = con.execute(
        "SELECT facility_status_id FROM facility_statuses WHERE status_code='ACTIVE'"
    ).fetchone()[0]
    fid = con.execute(
        """INSERT INTO managed_facilities
           (facility_uuid,facility_name,facility_type_id,facility_status_id)
           VALUES (?, '試験橋梁', ?, ?)""", (str(uuid4()), type_id, status_id)
    ).lastrowid
    other_fid = con.execute(
        """INSERT INTO managed_facilities
           (facility_uuid,facility_name,facility_type_id,facility_status_id)
           VALUES (?, '別の橋梁', ?, ?)""", (str(uuid4()), type_id, status_id)
    ).lastrowid
    cid = create_case(
        con, case_type_code="COMPLAINT_REQUEST", title="橋梁損傷の要望",
        received_on="2026-11-05", created_by_user_id=uid, facility_ids=(fid,),
    )
    con.commit()
    con.close()
    return path, uid, fid, other_fid, cid


def _app(fixture_data, role):
    path, uid, *_ = fixture_data
    app = Flask(__name__)


    @app.get("/dashboard")
    def dashboard():
        return "Test Dashboard"
    
    app.secret_key = "test-secret-not-for-production"
    user = None if role is None else {"id": uid, "roles": {role}}

    @contextmanager
    def connect():
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
        finally:
            con.close()

    app.register_blueprint(create_case_repair_ui_blueprint(
        read_connection=connect, write_connection=connect, current_user=lambda: user
    ))
    return app.test_client()


def _csrf(client, case_id):
    response = client.get(f"/case-repairs/cases/{case_id}")
    assert response.status_code == 200
    with client.session_transaction() as sess:
        return sess["case_repair_csrf"]


def test_viewer_can_read_but_cannot_write(fixture_data):
    _, _, fid, _, cid = fixture_data
    client = _app(fixture_data, "VIEWER")
    token = _csrf(client, cid)
    response = client.post(f"/case-repairs/cases/{cid}/create", data={
        "csrf_token": token, "facility_id": fid,
        "repair_name": "補修", "repair_type": "PLANNED", "repair_description": "",
    })
    assert response.status_code == 403


def test_unauthenticated_is_rejected(fixture_data):
    *_, cid = fixture_data
    client = _app(fixture_data, None)
    assert client.get(f"/case-repairs/cases/{cid}").status_code == 403


def test_csrf_is_required(fixture_data):
    _, _, fid, _, cid = fixture_data
    client = _app(fixture_data, "EDITOR")
    assert client.post(f"/case-repairs/cases/{cid}/create", data={
        "facility_id": fid, "repair_name": "補修", "repair_type": "PLANNED",
        "repair_description": "",
    }).status_code == 400


def test_create_repair_and_follow_bidirectional_links(fixture_data):
    path, _, fid, _, cid = fixture_data
    client = _app(fixture_data, "EDITOR")
    token = _csrf(client, cid)
    result = client.post(f"/case-repairs/cases/{cid}/create", data={
        "csrf_token": token, "facility_id": str(fid), "repair_name": "高欄補修",
        "repair_type": "PLANNED", "repair_description": "要望から登録",
    })
    assert result.status_code == 303
    with sqlite3.connect(path) as con:
        repair_id = con.execute("SELECT repair_id FROM facility_repairs").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM case_repair_links").fetchone()[0] == 1
    assert client.get(f"/case-repairs/repairs/{repair_id}").status_code == 200
    assert "高欄補修" in client.get(f"/case-repairs/cases/{cid}").get_data(as_text=True)


def test_create_cannot_use_facility_not_on_case(fixture_data):
    path, _, _, other_fid, cid = fixture_data
    client = _app(fixture_data, "ADMINISTRATOR")
    token = _csrf(client, cid)
    result = client.post(f"/case-repairs/cases/{cid}/create", data={
        "csrf_token": token, "facility_id": str(other_fid), "repair_name": "不正な修繕",
        "repair_type": "PLANNED", "repair_description": "",
    })
    assert result.status_code == 400
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM facility_repairs").fetchone()[0] == 0


def test_unlink_preserves_history(fixture_data):
    path, _, fid, _, cid = fixture_data
    client = _app(fixture_data, "EDITOR")
    token = _csrf(client, cid)
    client.post(f"/case-repairs/cases/{cid}/create", data={
        "csrf_token": token, "facility_id": str(fid), "repair_name": "補修",
        "repair_type": "PLANNED", "repair_description": "",
    })
    with sqlite3.connect(path) as con:
        rid = con.execute("SELECT repair_id FROM facility_repairs").fetchone()[0]
    result = client.post(f"/case-repairs/cases/{cid}/unlink", data={
        "csrf_token": token, "repair_id": str(rid), "reason": "関連先の訂正",
    })
    assert result.status_code == 303
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT unlinked_at IS NOT NULL FROM case_repair_links"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM case_repair_link_events"
        ).fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM facility_repairs").fetchone()[0] == 1
