"""Phase 10-12E-4: end-to-end authorization and session regression tests.

All fixtures use temporary, generated data. No municipality data is required.
"""

import sqlite3
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.web.app import create_app, create_local_user

YEAR = 2027
API = f"/api/v1/dashboard/fiscal?fiscal_year={YEAR}"
EXPORT = f"/api/v1/exports/budget?fiscal_year={YEAR}"
CARRYOVERS = f"/api/v1/exports/carryovers?fiscal_year={YEAR}"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "security.sqlite"
    connection = migrate.get_connection(path)
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    connection.close()
    return path


@pytest.fixture
def app(db_path):
    application = create_app(database_path=db_path, secret_key="s" * 48)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def _account(db_path, role="VIEWER", login="officer"):
    return create_local_user(
        db_path,
        login_id=login,
        password="test-password-123",
        display_name="テスト職員",
        role_code=role,
    )


def _login(client, login="officer", password="test-password-123"):
    assert client.get("/login").status_code == 200
    with client.session_transaction() as sess:
        token = sess["login_csrf"]
    return client.post(
        "/login",
        data={"login_id": login, "password": password, "csrf_token": token},
    )


def _write(db_path, sql, params=()):
    with sqlite3.connect(db_path) as connection:
        connection.execute(sql, params)


def _session_has_user(client):
    with client.session_transaction() as sess:
        return "user_id" in sess


@pytest.mark.parametrize("role", ["ADMINISTRATOR", "EDITOR", "VIEWER"])
def test_roles_can_read_screen_api_and_csv(client, db_path, role):
    _account(db_path, role)
    assert _login(client).status_code == 302
    assert client.get(f"/dashboard?fiscal_year={YEAR}").status_code == 200
    assert client.get(API).status_code == 200
    response = client.get(EXPORT)
    assert response.status_code == 200
    assert response.data.startswith(b"\xef\xbb\xbf")
    assert client.get(CARRYOVERS).status_code == 200


def test_anonymous_browser_and_downloads_are_denied(client):
    assert client.get("/dashboard").status_code == 302
    assert client.get(API).status_code == 403
    assert client.get(EXPORT).status_code == 403
    assert client.get(CARRYOVERS).status_code == 403


@pytest.mark.parametrize("column", ["is_locked", "is_active"])
def test_disabled_account_loses_all_access_and_session(client, db_path, column):
    uid = _account(db_path)
    assert _login(client).status_code == 302
    new_value = 1 if column == "is_locked" else 0
    _write(db_path, f"UPDATE users SET {column}=? WHERE user_id=?", (new_value, uid))
    assert client.get(API).status_code == 403
    assert not _session_has_user(client)
    assert client.get(EXPORT).status_code == 403
    assert client.get("/dashboard").status_code == 302


def test_revoked_role_loses_access_and_session(client, db_path):
    uid = _account(db_path)
    assert _login(client).status_code == 302
    _write(
        db_path,
        "UPDATE user_roles SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (uid,),
    )
    assert client.get(EXPORT).status_code == 403
    assert not _session_has_user(client)
    assert client.get(API).status_code == 403
    assert client.get("/dashboard").status_code == 302


def test_inactive_role_loses_access_and_session(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    _write(db_path, "UPDATE roles SET is_active=0 WHERE role_code='VIEWER'")
    assert client.get(API).status_code == 403
    assert not _session_has_user(client)


def test_logout_requires_csrf_and_revokes_login(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    assert client.post("/logout").status_code == 400
    assert client.get(API).status_code == 200
    with client.session_transaction() as sess:
        token = sess["logout_csrf"]
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert not _session_has_user(client)
    assert client.get(EXPORT).status_code == 403


def test_login_fails_closed_without_csrf(client, db_path):
    _account(db_path)
    assert client.post(
        "/login", data={"login_id": "officer", "password": "test-password-123"}
    ).status_code == 400
    assert client.get(API).status_code == 403


def test_failed_logins_block_subsequent_valid_password(client, db_path):
    _account(db_path)
    for _ in range(5):
        assert _login(client, password="wrong-password").status_code == 401
    assert _login(client).status_code == 401
    assert client.get(EXPORT).status_code == 403
    with sqlite3.connect(db_path) as con:
        count = con.execute(
            "SELECT COUNT(*) FROM user_login_history WHERE result_code='INVALID_CREDENTIALS'"
        ).fetchone()[0]
    assert count == 6


def test_unknown_user_does_not_gain_session(client):
    assert _login(client, login="missing-account").status_code == 401
    assert not _session_has_user(client)
    assert client.get(API).status_code == 403


def test_invalid_ids_and_repeated_params_never_export(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    for path in (
        "/api/v1/exports/budget?fiscal_year=1800",
        "/api/v1/exports/budget?fiscal_year=2027&fiscal_year=2028",
        "/api/v1/exports/contracts?fiscal_year=2027&project_id=-1",
        "/api/v1/exports/carryovers?fiscal_year=2027&carryover_type_code=BAD",
    ):
        assert client.get(path).status_code == 400
    assert client.get(
        "/api/v1/exports/budget?fiscal_year=2027&project_id=999999"
    ).status_code == 404


def test_get_reports_do_not_change_budget_tables(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    with sqlite3.connect(db_path) as con:
        project_id = con.execute(
            "INSERT INTO maintenance_projects (project_uuid, project_name) VALUES (?, ?)",
            (str(uuid4()), "検証用事業"),
        ).lastrowid
        con.execute(
            """INSERT INTO budget_requests (
                request_uuid, project_id, fiscal_year, request_title,
                original_requested_amount_yen, current_requested_amount_yen
            ) VALUES (?, ?, ?, '検証用予算', 1000, 1000)""",
            (str(uuid4()), project_id, YEAR),
        )
    with sqlite3.connect(db_path) as con:
        before = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("budget_requests", "budget_contract_allocations", "construction_payments")
        }
    assert client.get(API).status_code == 200
    assert client.get(EXPORT).status_code == 200
    assert client.get(CARRYOVERS).status_code == 200
    with sqlite3.connect(db_path) as con:
        after = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert before == after


def test_security_headers_on_download_and_errors(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    for path in (EXPORT, "/api/v1/exports/budget?fiscal_year=1800", "/login"):
        response = client.get(path)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_export_endpoints_never_accept_post(client, db_path):
    _account(db_path)
    assert _login(client).status_code == 302
    assert client.post(EXPORT).status_code == 405
    assert client.post(CARRYOVERS).status_code == 405
