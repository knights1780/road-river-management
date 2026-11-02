"""Flask application factory, local authentication and screen tests."""

from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

from road_river_management.database import migrate
from road_river_management.web.app import create_app, create_local_user


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "web.sqlite"
    connection = migrate.get_connection(path)
    migrate.ensure_schema_versions(connection)
    migrate.upgrade(connection)
    connection.close()
    return path


@pytest.fixture
def app(db_path):
    app = create_app(database_path=db_path, secret_key="x" * 48)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(db_path):
    return create_local_user(
        db_path, login_id="officer", password="sample-password",
        display_name="架空担当者", role_code="VIEWER",
    )


def login(client, login_id="officer", password="sample-password"):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["login_csrf"]
    return client.post(
        "/login", data={"login_id": login_id, "password": password, "csrf_token": token}
    )


def test_app_requires_explicit_configuration(db_path, tmp_path):
    with pytest.raises(ValueError):
        create_app(database_path=db_path, secret_key="weak")
    with pytest.raises(FileNotFoundError):
        create_app(database_path=tmp_path / "missing.sqlite", secret_key="x" * 48)


def test_anonymous_redirect_and_api_denial(client):
    assert client.get("/").status_code == 302
    assert client.get("/dashboard").status_code == 302
    assert client.get("/api/v1/dashboard/fiscal?fiscal_year=2027").status_code == 403


def test_user_login_and_dashboard(client, user):
    response = login(client)
    assert response.status_code == 302
    html = client.get("/dashboard?fiscal_year=2027")
    assert html.status_code == 200
    assert "予算ダッシュボード" in html.get_data(as_text=True)
    assert "架空担当者" in html.get_data(as_text=True)
    assert "script-src 'self'" in html.headers["Content-Security-Policy"]
    api = client.get("/api/v1/dashboard/fiscal?fiscal_year=2027")
    assert api.status_code == 200
    assert api.json["budget_year"]["request_count"] == 0


def test_login_rejects_invalid_password(client, user):
    response = login(client, password="incorrect")
    assert response.status_code == 401
    assert client.get("/api/v1/dashboard/fiscal?fiscal_year=2027").status_code == 403


def test_login_requires_csrf(client, user):
    assert client.post(
        "/login", data={"login_id": "officer", "password": "sample-password"}
    ).status_code == 400


def test_lock_disables_existing_session(client, user, db_path):
    login(client)
    con = migrate.get_connection(db_path)
    con.execute("UPDATE users SET is_locked=1 WHERE user_id=?", (user,))
    con.commit()
    con.close()
    assert client.get("/api/v1/dashboard/fiscal?fiscal_year=2027").status_code == 403
    assert client.get("/dashboard").status_code == 302


def test_role_revocation_disables_existing_session(client, user, db_path):
    login(client)
    con = migrate.get_connection(db_path)
    con.execute("UPDATE user_roles SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=?", (user,))
    con.commit()
    con.close()
    assert client.get("/api/v1/dashboard/fiscal?fiscal_year=2027").status_code == 403
    assert client.get("/dashboard").status_code == 403


def test_logout_csrf_and_session(client, user):
    login(client)
    assert client.post("/logout").status_code == 400
    with client.session_transaction() as sess:
        token = sess["logout_csrf"]
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/api/v1/dashboard/fiscal?fiscal_year=2027").status_code == 403


def test_dashboard_input_validation(client, user):
    login(client)
    assert client.get("/dashboard?fiscal_year=1800").status_code == 400
    assert client.get("/dashboard?fiscal_year=2027&fiscal_year=2028").status_code == 400
    assert client.get("/dashboard?project_id=abc").status_code == 400
    assert client.get("/dashboard?project_id=999999").status_code == 404


def test_create_user_role_and_password_hash(db_path):
    uid = create_local_user(
        db_path, login_id="tester", password="unrelated-pass",
        display_name="架空管理者", role_code="ADMINISTRATOR",
    )
    con = migrate.get_connection(db_path)
    row = con.execute(
        "SELECT password_hash, password_algorithm FROM users WHERE user_id=?", (uid,)
    ).fetchone()
    assert row[0] != "unrelated-pass"
    assert row[1] == "WERKZEUG"
    assert con.execute("SELECT COUNT(*) FROM user_roles WHERE user_id=?", (uid,)).fetchone()[0] == 1
    con.close()


def test_login_history_recorded(client, user, db_path):
    login(client, password="bad-password")
    login(client)
    con = migrate.get_connection(db_path)
    rows = con.execute(
        "SELECT result_code FROM user_login_history WHERE user_id=? ORDER BY login_history_id",
        (user,),
    ).fetchall()
    assert [r[0] for r in rows] == ["INVALID_CREDENTIALS", "SUCCESS"]
    con.close()


def test_project_filter_and_html_escaping(client, user, db_path):
    con = migrate.get_connection(db_path)
    project_id = con.execute(
        "INSERT INTO maintenance_projects (project_uuid, project_name) VALUES (?, ?)",
        (str(uuid4()), "<script>alert(1)</script>"),
    ).lastrowid
    con.commit()
    con.close()
    login(client)
    response = client.get(f"/dashboard?fiscal_year=2027&project_id={project_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_legacy_password_algorithm_is_not_accepted(client, db_path):
    con = migrate.get_connection(db_path)
    con.execute(
        """INSERT INTO users (user_uuid, login_id, display_name, password_hash,
            password_algorithm) VALUES (?, 'legacy', '旧利用者', ?, 'OTHER')""",
        (str(uuid4()), generate_password_hash("sample-password")),
    )
    con.commit()
    con.close()
    assert login(client, login_id="legacy").status_code == 401
