"""Read-only, fail-closed Flask dashboard API.

Integrators MUST provide both an authentication/authorization callback and a
SQLite connection factory. Nothing is opened by default.
"""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from flask import Blueprint, current_app, jsonify, request

from road_river_management.services.budget_dashboard import get_integrated_fiscal_dashboard
from road_river_management.services.carryover_checklist import get_carryover_checklist
from road_river_management.services.carryover_reports import get_carryover_list_report


def _integer(raw: str | None, name: str, *, required: bool = False) -> int | None:
    if raw is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{name} must be a positive decimal integer")
    value = int(raw)
    if name == "fiscal_year":
        if not 1900 <= value <= 2200:
            raise ValueError("fiscal_year is out of range")
    elif value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _params(allowed: set[str]) -> None:
    keys = set(request.args)
    if keys - allowed or any(len(request.args.getlist(key)) != 1 for key in keys):
        raise ValueError("Unknown or repeated query parameter")


def create_dashboard_blueprint(
    *,
    connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]] | None = None,
    can_view: Callable[[], bool] | None = None,
) -> Blueprint:
    """Create an API requiring explicit per-request authorization.

    connection_factory returns an enterable, already-open DB handle which is
    closed by its context manager. Read-only SQLite URI is recommended.
    can_view must resolve the current authenticated user's permission.
    """
    bp = Blueprint("dashboard_api", __name__, url_prefix="/api/v1/dashboard")

    @bp.before_request
    def check_access():
        if connection_factory is None or can_view is None:
            return jsonify(error="dashboard_not_configured"), 503
        try:
            permitted = can_view()
        except Exception:
            current_app.logger.exception("Dashboard authorization provider failed")
            return jsonify(error="authorization_unavailable"), 503
        if not permitted:
            return jsonify(error="forbidden"), 403
        return None

    @bp.errorhandler(ValueError)
    def invalid_query(exc: ValueError):
        return jsonify(error="invalid_request", detail=str(exc)), 400

    @bp.get("/fiscal")
    def fiscal():
        _params({"fiscal_year", "project_id"})
        year = _integer(request.args.get("fiscal_year"), "fiscal_year", required=True)
        project_id = _integer(request.args.get("project_id"), "project_id")
        with connection_factory() as connection:
            try:
                result = get_integrated_fiscal_dashboard(
                    connection, year, project_id=project_id
                )
            except ValueError as exc:
                if str(exc) == "Project not found":
                    return jsonify(error="project_not_found"), 404
                raise
        return jsonify(result)

    @bp.get("/carryovers")
    def carryovers():
        _params({"fiscal_year", "project_id", "carryover_type_code"})
        year = _integer(request.args.get("fiscal_year"), "fiscal_year", required=True)
        project_id = _integer(request.args.get("project_id"), "project_id")
        kind = request.args.get("carryover_type_code")
        with connection_factory() as connection:
            result = get_carryover_list_report(
                connection, year, carryover_type_code=kind, project_id=project_id
            )
        return jsonify(result)

    @bp.get("/carryovers/<int:carryover_id>/checklist")
    def checklist(carryover_id: int):
        _params({"reason_code"})
        reason = request.args.get("reason_code")
        if not reason:
            raise ValueError("reason_code is required")
        with connection_factory() as connection:
            try:
                result = get_carryover_checklist(
                    connection, carryover_id, reason_code=reason
                )
            except ValueError as exc:
                if str(exc) == "Carryover not found":
                    return jsonify(error="carryover_not_found"), 404
                raise
        return jsonify(result)

    @bp.after_request
    def prevent_cache(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return bp
