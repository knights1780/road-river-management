"""Read-only facility navigation, access control and linked URLs."""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask

from road_river_management.web.facility_detail_ui import create_facility_detail_blueprint


@pytest.fixture
def sample_detail():
    return {
        "facility": {
            "facility_id": 1,
            "facility_name": "テスト橋梁A",
            "facility_code": "DEMO-BRIDGE-001",
            "category_name": "道路",
            "type_name": "橋梁",
            "description": "表示確認用",
        },
        "repairs": [{
            "repair_id": 7, "repair_name": "高欄補修", "completed_on": None,
            "started_on": "2026-11-05", "repair_description": "補修作業",
        }],
        "cases": [{
            "case_id": 3, "title": "高欄の破損", "received_on": "2026-11-04",
            "status_code": "OPEN",
        }],
        "inspections": [],
        "components": [],
    }


@pytest.fixture
def client_factory(sample_detail):
    @contextmanager
    def factory(allowed=True, fail=False, missing=False):
        app = Flask(__name__, template_folder="../../src/road_river_management/web/templates")
        app.config["TESTING"] = True
        app.secret_key = "test-only"

        @app.get("/dashboard")
        def dashboard():
            return "dashboard"

        other = Blueprint("case_repair_ui", __name__)

        @other.get("/case-repairs/cases/<int:case_id>")
        def case_page(case_id):
            return str(case_id)

        @other.get("/case-repairs/repairs/<int:repair_id>")
        def repair_page(repair_id):
            return str(repair_id)

        app.register_blueprint(other)

        @contextmanager
        def connect():
            yield object()

        def permission():
            if fail:
                raise RuntimeError("authorization unavailable")
            return allowed

        app.register_blueprint(create_facility_detail_blueprint(
            connection_factory=connect, can_view=permission,
        ))
        def detail(con, fid):
            if missing or fid != 1:
                raise LookupError("not found")
            return sample_detail
        with patch("road_river_management.web.facility_detail_ui.get_facility_detail", detail):
            yield app.test_client()
    return factory


def test_facility_detail_navigates_to_existing_pages(client_factory):
    with client_factory() as client:
        response = client.get("/facilities/1")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "テスト橋梁A" in html
        assert 'href="/case-repairs/repairs/7"' in html
        assert 'href="/case-repairs/cases/3"' in html
        assert "高欄補修" in html
        assert "高欄の破損" in html


def test_facility_detail_denied(client_factory):
    with client_factory(allowed=False) as client:
        assert client.get("/facilities/1").status_code == 403


def test_facility_detail_auth_failure(client_factory):
    with client_factory(fail=True) as client:
        assert client.get("/facilities/1").status_code == 503


def test_missing_facility_returns_404(client_factory):
    with client_factory(missing=True) as client:
        assert client.get("/facilities/1").status_code == 404


def test_post_not_allowed(client_factory):
    with client_factory() as client:
        assert client.post("/facilities/1").status_code == 405
