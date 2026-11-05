"""Read-only facility detail screen and navigation to linked cases/repairs."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from flask import Blueprint, abort, current_app, render_template

from road_river_management.services.facility_overview import get_facility_detail


def create_facility_detail_blueprint(
    *,
    connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    can_view: Callable[[], bool],
) -> Blueprint:
    """Create independent detail page; authorization is checked for every request."""
    bp = Blueprint("facility_detail_ui", __name__, url_prefix="/facilities")

    @bp.before_request
    def authorize() -> None:
        try:
            allowed = can_view()
        except Exception:
            current_app.logger.exception("Facility detail authorization failed")
            abort(503)
        if not allowed:
            abort(403)

    @bp.get("/<int:facility_id>")
    def facility_page(facility_id: int):
        if facility_id <= 0:
            abort(404)
        with connection_factory() as con:
            try:
                detail = get_facility_detail(con, facility_id)
            except (LookupError, ValueError):
                abort(404)
        return render_template("facility_detail.html", detail=detail)

    return bp
