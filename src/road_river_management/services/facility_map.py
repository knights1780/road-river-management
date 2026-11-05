"""Read-only GeoJSON representation of persisted facility geometries."""

import json
import math
import sqlite3

GEOMETRY_TYPES = frozenset({
    "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"
})


def _valid_coordinates(value: object, *, depth: int = 0) -> bool:
    if depth > 7 or not isinstance(value, list) or not value:
        return False
    if all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value):
        return len(value) >= 2 and all(math.isfinite(n) for n in value)
    return all(_valid_coordinates(item, depth=depth + 1) for item in value)


def _parse_geometry(value: str, kind: str) -> dict | None:
    if not isinstance(value, str) or len(value) > 2_000_000 or kind not in GEOMETRY_TYPES:
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        if parsed.get("type") != kind:
            return None
        coords = parsed.get("coordinates")
    else:
        # Legacy records may store only the coordinate array.
        coords = parsed
    if not _valid_coordinates(coords):
        return None
    return {"type": kind, "coordinates": coords}


def get_facility_features(connection: sqlite3.Connection) -> dict:
    """Return one preferred geometry per facility. Exclude facilities without GIS data."""
    cursor = connection.execute("""
        SELECT f.facility_id, f.facility_name, f.facility_code,
               c.category_name, t.type_name,
               g.geometry_type, g.geometry_json
        FROM managed_facilities AS f
        JOIN facility_types AS t ON t.facility_type_id = f.facility_type_id
        JOIN facility_categories AS c ON c.facility_category_id = t.facility_category_id
        JOIN facility_geometries AS g ON g.facility_geometry_id = (
            SELECT g2.facility_geometry_id FROM facility_geometries AS g2
            WHERE g2.facility_id = f.facility_id
            ORDER BY g2.is_primary DESC, g2.facility_geometry_id ASC LIMIT 1
        )
        ORDER BY c.sort_order, t.sort_order, f.facility_id
    """)
    features = []
    omitted_invalid = 0
    for fid, name, code, category, typ, geometry_type, raw in cursor:
        geometry = _parse_geometry(raw, geometry_type)
        if geometry is None:
            omitted_invalid += 1
            continue
        features.append({
            "type": "Feature",
            "id": fid,
            "geometry": geometry,
            "properties": {
                "facility_id": fid,
                "facility_name": name,
                "facility_code": code,
                "category_name": category,
                "type_name": typ,
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "omitted_invalid_geometry_count": omitted_invalid,
    }
