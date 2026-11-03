"""Authenticated offline raster tile delivery with optional PC-local cache.

Only allowlisted MBTiles are read. No internet or external tile service is used.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify

from road_river_management.web.map_tile_cache import LocalTileCache

PNG = b"\x89PNG\r\n\x1a\n"


def create_map_tiles_blueprint(
    *,
    sources: Mapping[str, str | Path] | None = None,
    can_view: Callable[[], bool] | None = None,
    cache: LocalTileCache | None = None,
    versions: Mapping[str, str] | None = None,
) -> Blueprint:
    """Build tile endpoints with fixed allowlist and per-request authorization.

    Caching requires an explicit immutable release version per layer.
    Layers lacking a release version always bypass the cache.
    """
    approved = {}
    for key, value in (sources or {}).items():
        if not isinstance(key, str) or not key.isascii() or not key.replace("_", "").isalnum():
            raise ValueError("Map layer names must be ASCII letters, digits or underscores")
        approved[key] = Path(value).expanduser().resolve()

    releases = dict(versions or {})
    for key, version in releases.items():
        if key not in approved or not isinstance(version, str) or not version.strip():
            raise ValueError("Map release versions require a configured layer and nonempty version")
        if len(version) > 100:
            raise ValueError("Map release version exceeds 100 characters")

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

        version = releases.get(layer)
        if cache is not None and version is not None:
            try:
                cached = cache.get(layer, version, z, x, y)
                if cached is not None:
                    return Response(cached, mimetype="image/png")
            except (OSError, sqlite3.Error):
                current_app.logger.warning("Local tile cache read failed", exc_info=True)

        db_path = approved[layer]
        if not db_path.is_file():
            return jsonify(error="map_unavailable"), 503

        try:
            # Source releases must be finalized and immutable; never edit them in place.
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

        if cache is not None and version is not None:
            try:
                cache.put(layer, version, z, x, y, data)
            except (OSError, sqlite3.Error):
                current_app.logger.warning("Local tile cache write failed", exc_info=True)
        # Global Flask after_request enforces no-store, preventing shared browser caches.
        return Response(data, mimetype="image/png")

    return bp
