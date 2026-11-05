"""Smoke tests for tree, progressive summary/detail and access controls."""

import sqlite3
from contextlib import contextmanager

import pytest
from flask import Flask

from road_river_management.services.facility_overview import (
    get_facility_detail,
    get_facility_summary,
    list_facility_tree,
)
from road_river_management.web.facility_api import create_facility_blueprint


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "facilities.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE facility_categories (facility_category_id INTEGER PRIMARY KEY,
                category_code TEXT, category_name TEXT, sort_order INTEGER);
            CREATE TABLE facility_types (facility_type_id INTEGER PRIMARY KEY,
                facility_category_id INTEGER, type_code TEXT, type_name TEXT,
                sort_order INTEGER);
            CREATE TABLE managed_facilities (facility_id INTEGER PRIMARY KEY,
                facility_type_id INTEGER, facility_code TEXT, facility_name TEXT,
                description TEXT, installed_on TEXT, abolished_on TEXT,
                facility_status_id INTEGER, responsible_department_id INTEGER);
            CREATE TABLE facility_components (component_id INTEGER PRIMARY KEY,
                facility_id INTEGER, parent_component_id INTEGER, component_name TEXT,
                component_type TEXT, is_active INTEGER, component_code TEXT,
                manufacturer TEXT, model_number TEXT, serial_number TEXT,
                installed_on TEXT, abolished_on TEXT, remarks TEXT);
            CREATE TABLE bridge_details (facility_id INTEGER PRIMARY KEY,
                bridge_length_m REAL);
            CREATE TABLE facility_repairs (repair_id INTEGER PRIMARY KEY,
                facility_id INTEGER, repair_name TEXT, repair_type TEXT,
                completed_on TEXT, started_on TEXT, repair_description TEXT,
                actual_cost_yen INTEGER, completion_result TEXT);
            CREATE TABLE facility_inspections (inspection_id INTEGER PRIMARY KEY,
                facility_id INTEGER, inspection_date TEXT, inspection_name TEXT,
                inspection_type TEXT, inspection_method TEXT, inspection_result TEXT,
                condition_rating TEXT, next_inspection_due_date TEXT);
            CREATE TABLE administrative_cases (case_id INTEGER PRIMARY KEY,
                title TEXT, case_type_code TEXT, status_code TEXT,
                received_on TEXT, importance_code TEXT);
            CREATE TABLE administrative_case_facilities (case_id INTEGER,
                facility_id INTEGER);
            CREATE TABLE component_maintenance_history (
                component_maintenance_id INTEGER PRIMARY KEY, component_id INTEGER,
                maintenance_date TEXT, maintenance_type TEXT, maintenance_name TEXT,
                maintenance_description TEXT, cost_yen INTEGER,
                result_description TEXT);
            INSERT INTO facility_categories VALUES (1, 'ROAD', '道路', 1);
            INSERT INTO facility_types VALUES (1, 1, 'BRIDGE', '橋梁', 1);
            INSERT INTO managed_facilities VALUES (1, 1, 'B-1', '第一橋', NULL,
                NULL, NULL, 1, NULL);
            INSERT INTO bridge_details VALUES (1, 17.5);
            INSERT INTO facility_components VALUES (10,1,NULL,'高欄','RAILING',1,
                'C10',NULL,NULL,NULL,NULL,NULL,NULL);
            INSERT INTO facility_components VALUES (11,1,10,'支柱','POST',1,
                'C11',NULL,NULL,NULL,NULL,NULL,NULL);
            INSERT INTO facility_repairs VALUES (20,1,'高欄補修','PLANNED',
                '2025-08-01',NULL,NULL,10000,NULL);
            INSERT INTO facility_inspections VALUES (30,1,'2025-07-01',
                '定期点検','PERIODIC','DIRECT',NULL,NULL,NULL);
            INSERT INTO administrative_cases VALUES (40,'高欄要望',
                'COMPLAINT_REQUEST','OPEN','2025-07-02','NORMAL');
            INSERT INTO administrative_case_facilities VALUES (40,1);
            INSERT INTO component_maintenance_history VALUES (50,10,
                '2025-08-01','REPAIR','高欄部品交換',NULL,3000,NULL);
        """)
    return path


def test_summary_and_detail_are_separate(database):
    with sqlite3.connect(database) as con:
        summary = get_facility_summary(con, 1)
        detail = get_facility_detail(con, 1)
    assert summary["repair_count"] == 1
    assert summary["active_case_count"] == 1
    assert summary["last_inspection_on"] == "2025-07-01"
    assert summary["photo"] is None
    assert "components" not in summary
    assert detail["attributes"]["bridge_length_m"] == 17.5
    assert len(detail["components"]) == 2
    assert len(detail["component_maintenance"]) == 1
    assert detail["repair_case_links_supported"] is False


def test_tree_nests_components(database):
    with sqlite3.connect(database) as con:
        categories = list_facility_tree(con)["categories"]
    roots = categories[0]["types"][0]["facilities"][0]["components"]
    assert roots[0]["component_id"] == 10
    assert roots[0]["children"][0]["component_id"] == 11


def test_facility_not_found(database):
    with (
        sqlite3.connect(database) as con,
        pytest.raises(LookupError, match="Facility not found"),
    ):
        get_facility_summary(con, 999)


def test_api_requires_access(database):
    app = Flask(__name__)

    @contextmanager
    def connect():
        con = sqlite3.connect(database)
        try:
            yield con
        finally:
            con.close()

    app.register_blueprint(create_facility_blueprint(
        connection_factory=connect, can_view=lambda: False,
    ))
    client = app.test_client()
    assert client.get("/api/v1/facilities/tree").status_code == 403
    assert client.get("/api/v1/facilities/1/summary").status_code == 403


def test_api_success_and_missing(database):
    app = Flask(__name__)

    @contextmanager
    def connect():
        con = sqlite3.connect(database)
        try:
            yield con
        finally:
            con.close()

    app.register_blueprint(create_facility_blueprint(
        connection_factory=connect, can_view=lambda: True,
    ))
    client = app.test_client()
    assert client.get("/api/v1/facilities/tree").status_code == 200
    assert client.get("/api/v1/facilities/1/summary").json["repair_count"] == 1
    assert client.get("/api/v1/facilities/1/detail").json["attributes"]["bridge_length_m"] == 17.5
    assert client.get("/api/v1/facilities/999/summary").status_code == 404
