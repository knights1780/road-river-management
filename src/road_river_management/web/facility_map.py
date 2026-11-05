"""Local-only facility map UI and read-only GeoJSON API."""

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from flask import Blueprint, current_app, jsonify, render_template

from road_river_management.services.facility_map import get_facility_features


def create_facility_map_blueprint(
    *,
    connection_factory: Callable[[], AbstractContextManager[sqlite3.Connection]],
    can_view: Callable[[], bool],
    map_layer: str = "demo",
) -> Blueprint:
    """Fail closed; uses the application's existing login/session provider."""
    bp = Blueprint("facility_map", __name__)

    @bp.before_request
    def authorize():
        try:
            authorized = can_view()
        except Exception:
            current_app.logger.exception("Facility map authorization failed")
            return jsonify(error="authorization_unavailable"), 503
        if not authorized:
            return jsonify(error="forbidden"), 403
        return None

    @bp.get("/facilities/map")
    def page():
        return render_template("facility_map.html", map_layer=map_layer)

    @bp.get("/api/v1/facilities/geojson")
    def geojson():
        with connection_factory() as connection:
            return jsonify(get_facility_features(connection))

    return bp
