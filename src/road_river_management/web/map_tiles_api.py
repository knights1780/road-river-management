"""Authenticated offline raster tile delivery from explicitly configured MBTiles.

Only configured local files are opened. No external tile service is accessed.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"


def create_map_tiles_blueprint(
    *,
    sources: Mapping[str, str | Path] | None = None,
    can_view: Callable[[], bool] | None = None,
) -> Blueprint:
    """Build tile endpoints with a fixed allowlist and per-request access check."""
    approved = {}
    for key, value in (sources or {}).items():
        if not isinstance(key, str) or not key.isascii() or not key.replace("_", "").isalnum():
            raise ValueError("Map layer names must be ASCII letters, digits or underscores")
        approved[key] = Path(value).expanduser().resolve()

    bp = Blueprint("map_tiles_api", __name__, url_prefix="/api/v1/maps")

    @bp.before_request
    def authorization():
        if can_view is None:
            return jsonify(error="map_authorization_not_configured"), 503
        try:
            permitted = can_view()
        except Exception:
            current_app.logger.exception("Map authorization failed")
            return jsonify(error="authorization_unavailable"), 503
        if not permitted:
            return jsonify(error="forbidden"), 403
        return None

    @bp.get("/<layer>/<int:z>/<int:x>/<int:y>.png")
    def tile(layer: str, z: int, x: int, y: int):
        if layer not in approved:
            return jsonify(error="unknown_layer"), 404
        if z > 22 or not 0 <= x < (1 << z) or not 0 <= y < (1 << z):
            return jsonify(error="invalid_tile_coordinates"), 400
        db_path = approved[layer]
        if not db_path.is_file():
            return jsonify(error="map_unavailable"), 503

        try:
            # SQLite immutable=1 avoids any journal write on a read-only NAS.
            # Release files must be fully finalized and never modified in place.
            uri = db_path.as_uri() + "?mode=ro&immutable=1"
            with sqlite3.connect(uri, uri=True, timeout=3) as connection:
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(
                    """SELECT tile_data FROM tiles
                       WHERE zoom_level=? AND tile_column=? AND tile_row=?""",
                    (z, x, (1 << z) - 1 - y),
                ).fetchone()
        except (OSError, sqlite3.Error):
            current_app.logger.exception("Tile source unavailable for %s", layer)
            return jsonify(error="map_unavailable"), 503

        if row is None:
            return jsonify(error="tile_not_found"), 404
        data = row[0]
        if not isinstance(data, bytes) or not data.startswith(PNG):
            current_app.logger.error("Non-PNG tile in layer %s", layer)
            return jsonify(error="tile_format_not_supported"), 503
        # Session-specific maps must never be cached by shared proxies.
        return Response(data, mimetype="image/png", headers={"Cache-Control": "private, no-store"})

    return bp
