"""Web integration tests for CSV downloads and authentication."""

import csv
import io
from uuid import uuid4

import pytest

from road_river_management.database import migrate
from road_river_management.web.app import create_app, create_local_user


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "exports.sqlite"
    con = migrate.get_connection(path)
    migrate.ensure_schema_versions(con)
    migrate.upgrade(con)
    con.close()
    return path


@pytest.fixture
def client(db_path):
    create_local_user(
        db_path, login_id="reader", password="local-test-password",
        display_name="架空閲覧者", role_code="VIEWER",
    )
    app = create_app(database_path=db_path, secret_key="x" * 48)
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["login_csrf"]
    return client.post(
        "/login",
        data={
            "login_id": "reader", "password": "local-test-password",
            "csrf_token": token,
        },
    )


def read_rows(response):
    return list(csv.DictReader(io.StringIO(response.data.decode("utf-8-sig"))))


def test_anonymous_exports_forbidden(client):
    urls = [
        "/api/v1/exports/budget?fiscal_year=2027",
        "/api/v1/exports/contracts?fiscal_year=2027",
        "/api/v1/exports/payments?fiscal_year=2027",
        "/api/v1/exports/carryovers?fiscal_year=2027",
        "/api/v1/exports/carryovers/1/reason",
        "/api/v1/exports/carryovers/1/review?reason_code=LAND",
    ]
    assert all(client.get(path).status_code == 403 for path in urls)


def test_download_four_lists_and_bom(client):
    login(client)
    for endpoint in ("budget", "contracts", "payments", "carryovers"):
        response = client.get(f"/api/v1/exports/{endpoint}?fiscal_year=2027")
        assert response.status_code == 200
        assert response.data.startswith(b"\xef\xbb\xbf")
        assert response.headers["Cache-Control"] == "no-store"
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_download_page_links(client):
    login(client)
    page = client.get("/dashboard?fiscal_year=2027")
    assert page.status_code == 200
    assert "予算執行状況表 CSV" in page.get_data(as_text=True)
    assert "繰越事業一覧 CSV" in page.get_data(as_text=True)


def test_repeated_and_unknown_parameters_rejected(client):
    login(client)
    assert client.get(
        "/api/v1/exports/budget?fiscal_year=2027&fiscal_year=2028"
    ).status_code == 400
    assert client.get(
        "/api/v1/exports/payments?fiscal_year=2027&unused=1"
    ).status_code == 400


def test_invalid_year_and_project_rejected(client):
    login(client)
    assert client.get("/api/v1/exports/budget?fiscal_year=").status_code == 400
    assert client.get("/api/v1/exports/budget?fiscal_year=1800").status_code == 400
    assert client.get(
        "/api/v1/exports/contracts?fiscal_year=2027&project_id=abc"
    ).status_code == 400
    assert client.get(
        "/api/v1/exports/budget?fiscal_year=2027&project_id=99999"
    ).status_code == 404


def test_invalid_carryover_type_rejected(client):
    login(client)
    response = client.get(
        "/api/v1/exports/carryovers?fiscal_year=2027&carryover_type_code=BAD"
    )
    assert response.status_code == 400


def test_missing_carryover_rejected(client):
    login(client)
    assert client.get("/api/v1/exports/carryovers/999/reason").status_code == 404
    assert client.get(
        "/api/v1/exports/carryovers/999/review?reason_code=LAND"
    ).status_code == 404


def test_review_requires_valid_reason(client):
    login(client)
    assert client.get("/api/v1/exports/carryovers/1/review").status_code == 400
    assert client.get(
        "/api/v1/exports/carryovers/1/review?reason_code=INVALID"
    ).status_code == 400


def test_carryover_reason_and_review_download(client, db_path):
    con = migrate.get_connection(db_path)
    project = con.execute(
        "INSERT INTO maintenance_projects (project_uuid, project_name) VALUES (?, ?)",
        (str(uuid4()), "架空事業"),
    ).lastrowid
    request_id = con.execute(
        """INSERT INTO budget_requests (request_uuid, project_id, fiscal_year,
            request_title, original_requested_amount_yen, current_requested_amount_yen)
            VALUES (?, ?, 2027, '架空予算', 1000000, 1000000)""",
        (str(uuid4()), project),
    ).lastrowid
    carryover_id = con.execute(
        """INSERT INTO budget_carryovers (carryover_uuid, budget_request_id,
            carryover_type_code, source_fiscal_year, destination_fiscal_year,
            carryover_reason) VALUES (?, ?, 'MEIKYO', 2027, 2028, '架空の理由')""",
        (str(uuid4()), request_id),
    ).lastrowid
    con.commit()
    con.close()
    login(client)
    reason = client.get(f"/api/v1/exports/carryovers/{carryover_id}/reason")
    review = client.get(
        f"/api/v1/exports/carryovers/{carryover_id}/review?reason_code=LAND"
    )
    assert reason.status_code == 200
    assert review.status_code == 200
    assert any(row["項目"] == "繰越理由" for row in read_rows(reason))
    assert any(row["コード"] == "LAND_NEGOTIATION" for row in read_rows(review))


def test_get_is_read_only(client, db_path):
    login(client)
    con = migrate.get_connection(db_path)
    before = con.total_changes
    con.close()
    client.get("/api/v1/exports/budget?fiscal_year=2027")
    con = migrate.get_connection(db_path)
    assert con.total_changes == before
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_post_not_allowed(client):
    login(client)
    assert client.post("/api/v1/exports/budget?fiscal_year=2027").status_code == 405
