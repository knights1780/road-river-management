"""HTTP-level tests for the fail-closed dashboard blueprint."""

from contextlib import contextmanager

import pytest
from flask import Flask

from road_river_management.web.dashboard_api import create_dashboard_blueprint


@contextmanager
def fake_connection():
    yield object()


@pytest.fixture
def app(monkeypatch):
    import road_river_management.web.dashboard_api as api

    monkeypatch.setattr(api, "get_integrated_fiscal_dashboard", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(api, "get_carryover_list_report", lambda *a, **k: {"records": []})
    monkeypatch.setattr(api, "get_carryover_checklist", lambda *a, **k: {"data_complete": False})
    application = Flask(__name__)
    application.register_blueprint(
        create_dashboard_blueprint(
            connection_factory=fake_connection,
            can_view=lambda: True,
        )
    )
    return application


def test_fiscal_json(app):
    response = app.test_client().get("/api/v1/dashboard/fiscal?fiscal_year=2027")
    assert response.status_code == 200
    assert response.json == {"ok": True}
    assert response.headers["Cache-Control"] == "no-store"


def test_carryover_json(app):
    response = app.test_client().get("/api/v1/dashboard/carryovers?fiscal_year=2027")
    assert response.status_code == 200
    assert response.json == {"records": []}


def test_checklist_json(app):
    response = app.test_client().get(
        "/api/v1/dashboard/carryovers/1/checklist?reason_code=LAND"
    )
    assert response.status_code == 200
    assert response.json == {"data_complete": False}


@pytest.mark.parametrize("query", [
    "", "?fiscal_year=abc", "?fiscal_year=1800", "?fiscal_year=2027&other=1",
    "?fiscal_year=2027&fiscal_year=2028", "?fiscal_year=2027&project_id=-1",
])
def test_invalid_fiscal_arguments(app, query):
    assert app.test_client().get("/api/v1/dashboard/fiscal" + query).status_code == 400


def test_missing_reason(app):
    assert app.test_client().get(
        "/api/v1/dashboard/carryovers/1/checklist"
    ).status_code == 400


def test_refuse_unconfigured():
    application = Flask(__name__)
    application.register_blueprint(create_dashboard_blueprint())
    assert application.test_client().get(
        "/api/v1/dashboard/fiscal?fiscal_year=2027"
    ).status_code == 503


def test_refuse_unauthorized():
    application = Flask(__name__)
    application.register_blueprint(create_dashboard_blueprint(
        connection_factory=fake_connection, can_view=lambda: False
    ))
    assert application.test_client().get(
        "/api/v1/dashboard/fiscal?fiscal_year=2027"
    ).status_code == 403


def test_refuse_auth_provider_exception():
    application = Flask(__name__)

    def fail():
        raise RuntimeError("auth unavailable")

    application.register_blueprint(create_dashboard_blueprint(
        connection_factory=fake_connection, can_view=fail
    ))
    assert application.test_client().get(
        "/api/v1/dashboard/fiscal?fiscal_year=2027"
    ).status_code == 503
