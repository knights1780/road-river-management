"""Authenticated, read-only API for progressive facility drill-down."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from flask import Blueprint, current_app, jsonify

from road_river_management.services.facility_overview import (
    get_facility_detail,
    get_facility_summary,
    list_facility_tree,
)


def create_facility_blueprint(
    *,
    connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    can_view: Callable[[], bool],
) -> Blueprint:
    bp = Blueprint("facility_api", __name__, url_prefix="/api/v1/facilities")

    @bp.before_request
    def authorize():
        try:
            permitted = can_view()
        except Exception:
            current_app.logger.exception("Facility authorization unavailable")
            return jsonify(error="authorization_unavailable"), 503
        if not permitted:
            return jsonify(error="forbidden"), 403
        return None

    @bp.get("/tree")
    def tree():
        with connection_factory() as con:
            return jsonify(list_facility_tree(con))

    @bp.get("/<int:facility_id>/summary")
    def summary(facility_id: int):
        with connection_factory() as con:
            try:
                return jsonify(get_facility_summary(con, facility_id))
            except (LookupError, ValueError):
                return jsonify(error="facility_not_found"), 404

    @bp.get("/<int:facility_id>/detail")
    def detail(facility_id: int):
        with connection_factory() as con:
            try:
                return jsonify(get_facility_detail(con, facility_id))
            except (LookupError, ValueError):
                return jsonify(error="facility_not_found"), 404

    return bp
