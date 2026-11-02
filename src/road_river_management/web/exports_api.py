"""Authenticated, read-only CSV export endpoints for the offline Flask UI."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from io import BytesIO

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from road_river_management.services.budget_csv import (
    export_contract_allocation_csv,
    export_fiscal_budget_csv,
    export_payment_year_csv,
)
from road_river_management.services.carryover_checklist import REASONS
from road_river_management.services.carryover_reports import (
    export_carryover_list_csv,
    export_carryover_reason_csv,
    export_carryover_review_csv,
)


def _number(name: str, *, year: bool = False, required: bool = False) -> int | None:
    raw = request.args.get(name)
    if raw is None or (raw == "" and not required):
        if required:
            raise ValueError(f"{name} is required")
        return None
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{name} must be a decimal integer")
    result = int(raw)
    if (year and not 1900 <= result <= 2200) or (not year and result <= 0):
        raise ValueError(f"{name} is out of range")
    return result


def _query(allowed: set[str]) -> None:
    supplied = set(request.args)
    if supplied - allowed or any(len(request.args.getlist(k)) != 1 for k in supplied):
        raise ValueError("Unknown or repeated query parameter")


def _download(content: bytes, filename: str):
    return send_file(
        BytesIO(content), mimetype="text/csv; charset=utf-8", as_attachment=True,
        download_name=filename, max_age=0,
    )


def create_exports_blueprint(
    *,
    connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]] | None = None,
    can_view: Callable[[], bool] | None = None,
) -> Blueprint:
    """Require explicit authorization and a read-only connection provider."""
    bp = Blueprint("exports", __name__, url_prefix="/api/v1/exports")

    @bp.before_request
    def authorize():
        if connection_factory is None or can_view is None:
            return jsonify(error="exports_not_configured"), 503
        try:
            permitted = can_view()
        except Exception:
            current_app.logger.exception("CSV export authorization failed")
            return jsonify(error="authorization_unavailable"), 503
        if not permitted:
            return jsonify(error="forbidden"), 403
        return None

    @bp.errorhandler(ValueError)
    def invalid_query(exc: ValueError):
        return jsonify(error="invalid_request", detail=str(exc)), 400

    @bp.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("/budget")
    def budget():
        _query({"fiscal_year", "project_id"})
        year = _number("fiscal_year", year=True, required=True)
        project_id = _number("project_id")
        with connection_factory() as con:
            try:
                data = export_fiscal_budget_csv(con, year, project_id=project_id)
            except ValueError as exc:
                if str(exc) == "Project not found":
                    abort(404)
                raise
        return _download(data, f"{year}_budget_execution.csv")

    @bp.get("/contracts")
    def contracts():
        _query({"fiscal_year", "project_id"})
        year = _number("fiscal_year", year=True, required=True)
        project_id = _number("project_id")
        with connection_factory() as con:
            try:
                data = export_contract_allocation_csv(con, year, project_id=project_id)
            except ValueError as exc:
                if str(exc) == "Project not found":
                    abort(404)
                raise
        return _download(data, f"{year}_contract_allocations.csv")

    @bp.get("/payments")
    def payments():
        _query({"fiscal_year"})
        year = _number("fiscal_year", year=True, required=True)
        with connection_factory() as con:
            data = export_payment_year_csv(con, year)
        return _download(data, f"{year}_payments.csv")

    @bp.get("/carryovers")
    def carryovers():
        _query({"fiscal_year", "project_id", "carryover_type_code"})
        year = _number("fiscal_year", year=True, required=True)
        project_id = _number("project_id")
        kind = request.args.get("carryover_type_code") or None
        with connection_factory() as con:
            data = export_carryover_list_csv(
                con, year, project_id=project_id, carryover_type_code=kind,
            )
        return _download(data, f"{year}_carryovers.csv")

    @bp.get("/carryovers/<int:carryover_id>/reason")
    def carryover_reason(carryover_id: int):
        _query(set())
        with connection_factory() as con:
            try:
                data = export_carryover_reason_csv(con, carryover_id)
            except ValueError as exc:
                if str(exc) == "Carryover not found":
                    abort(404)
                raise
        return _download(data, f"carryover_{carryover_id}_reason.csv")

    @bp.get("/carryovers/<int:carryover_id>/review")
    def carryover_review(carryover_id: int):
        _query({"reason_code"})
        reason = request.args.get("reason_code")
        if not reason:
            raise ValueError("reason_code is required")
        if reason not in REASONS:
            raise ValueError("Invalid carryover reason")
        with connection_factory() as con:
            try:
                data = export_carryover_review_csv(
                    con, carryover_id, reason_code=reason,
                )
            except ValueError as exc:
                if str(exc) == "Carryover not found":
                    abort(404)
                raise
        return _download(data, f"carryover_{carryover_id}_review.csv")

    return bp
