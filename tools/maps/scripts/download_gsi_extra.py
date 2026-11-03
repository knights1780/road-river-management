"""Standalone M4 supplementary tile plan and controlled retry tool.

No HTTP requests unless --execute --terms-reviewed. GSI zoom ranges checked
against the published layer catalog, 2026-11-04. Unsupported zooms are
included in the plan for auditing, but are never requested by this program.
No changes to original download script, log DB or summary JSON.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

BASE = "https://cyberjapandata.gsi.go.jp/xyz"
# (file extension, published min zoom, published max zoom)
PUBLISHED = {
    "blank": ("png", 5, 14),
    "seamlessphoto": ("jpg", 14, 18),
    "lcm25k_2012": ("png", 10, 16),
    "lcmfc2": ("png", 11, 16),
    "relief": ("png", 5, 15),
    "swale": ("png", 10, 16),
}
EXTRA_RANGES = {
    "blank": ((15, 18),),
    "seamlessphoto": ((5, 13),),
    "lcm25k_2012": ((5, 9), (17, 18)),
    "lcmfc2": ((5, 10), (17, 18)),
    "relief": ((17, 18),),
}
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
MAX_BYTES = 5 * 1024 * 1024
RETRY_LAYERS = frozenset(("lcm25k_2012", "lcmfc2", "swale"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_image(data: bytes, ext: str) -> bool:
    if ext == "png":
        return data.startswith(PNG) and len(data) > 24 and data[-8:-4] == b"IEND"
    return data.startswith(JPEG) and data.endswith(b"\xff\xd9") and len(data) > 100


def read_area(path: Path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    kind = doc.get("type")
    if kind == "FeatureCollection":
        polygons = [shape(f["geometry"]) for f in doc["features"] if f.get("geometry")]
        geom = unary_union(polygons)
    elif kind == "Feature":
        geom = shape(doc["geometry"])
    else:
        geom = shape(doc)
    if geom.is_empty or not geom.is_valid or geom.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Expected a valid nonempty Polygon/MultiPolygon AOI")
    return geom


def lonlat_to_xyz(lon: float, lat: float, z: int):
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 1 << z
    x = min(n - 1, max(0, int((lon + 180) / 360 * n)))
    radians = math.radians(lat)
    y = min(n - 1, max(0, int((1 - math.asinh(math.tan(radians)) / math.pi) / 2 * n)))
    return x, y


def tile_box(z: int, x: int, y: int):
    n = 1 << z
    west = 360 * x / n - 180
    east = 360 * (x + 1) / n - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return box(west, south, east, north)


def tiles_for_zoom(area, z: int):
    west, south, east, north = area.bounds
    x0, y0 = lonlat_to_xyz(west, north, z)
    x1, y1 = lonlat_to_xyz(east, south, z)
    prepared = prep(area)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if prepared.intersects(tile_box(z, x, y)):
                yield x, y


def retry_keys(log_path: Path):
    with sqlite3.connect(f"{log_path.resolve().as_uri()}?mode=ro", uri=True) as con:
        rows = con.execute(
            """SELECT layer,z,x,y FROM attempts
               WHERE result='http_error' AND status=404
               ORDER BY layer,z,x,y"""
        ).fetchall()
    keys = []
    for layer, z, x, y in rows:
        if layer not in RETRY_LAYERS:
            raise ValueError(f"Unexpected 404 layer: {layer}")
        ext, lower, upper = PUBLISHED[layer]
        if not lower <= z <= upper or not 0 <= x < (1 << z) or not 0 <= y < (1 << z):
            raise ValueError(f"Invalid retry coordinates: {layer} {z} {x} {y}")
        keys.append(("retry_404", layer, z, x, y, "published", ext))
    if len(keys) != 66 or len(set(keys)) != 66:
        raise ValueError(f"Expected exactly 66 distinct original HTTP 404 errors, got {len(keys)}")
    return keys


def make_plan(area_path: Path, log_path: Path):
    area = read_area(area_path)
    plan = retry_keys(log_path)
    for layer, ranges in EXTRA_RANGES.items():
        ext, lower, upper = PUBLISHED[layer]
        for start, end in ranges:
            for z in range(start, end + 1):
                status = "published" if lower <= z <= upper else "unsupported_zoom"
                for x, y in tiles_for_zoom(area, z):
                    plan.append(("extra_zoom", layer, z, x, y, status, ext))
    if len({(row[1], *row[2:5]) for row in plan}) != len(plan):
        raise ValueError("Duplicate tile in combined plan")
    return plan


def write_plan(plan, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    path = output / "extra_tile_plan.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["kind", "layer", "z", "x", "y", "availability", "extension"])
        writer.writerows(plan)
    return path


def tile_path(download_root: Path, row):
    _, layer, z, x, y, _, ext = row
    return download_root / layer / str(z) / str(x) / f"{y}.{ext}"


def log_connection(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS attempts (
        kind TEXT NOT NULL, layer TEXT NOT NULL, z INTEGER NOT NULL,
        x INTEGER NOT NULL, y INTEGER NOT NULL, result TEXT NOT NULL,
        status INTEGER, byte_count INTEGER, sha256 TEXT,
        detail TEXT, attempted_at TEXT NOT NULL,
        PRIMARY KEY (layer,z,x,y)
    )""")
    con.commit()
    return con


def record(con, row, result, *, status=None, data=None, detail=""):
    kind, layer, z, x, y, _, _ = row
    con.execute("""INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(layer,z,x,y) DO UPDATE SET
        result=excluded.result,status=excluded.status,
        byte_count=excluded.byte_count,sha256=excluded.sha256,
        detail=excluded.detail,attempted_at=excluded.attempted_at""",
        (kind, layer, z, x, y, result, status, len(data) if data is not None else None,
         hashlib.sha256(data).hexdigest() if data is not None else None,
         detail[:250], datetime.now(timezone.utc).isoformat()))
    con.commit()


def write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".extra.partial")
    if temp.exists():
        temp.unlink()  # Only this tool's interrupted temporary file.
    with temp.open("xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def fetch(opener, url: str, timeout: float):
    request = Request(url, headers={"User-Agent": "RoadRiverManagement-OfflineMaps/1.0",
                                    "Accept": "image/png,image/jpeg"})
    with opener.open(request, timeout=timeout) as response:
        size_header = response.headers.get("Content-Length")
        if size_header and int(size_header) > MAX_BYTES:
            raise ValueError("Tile too large")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Tile too large")
        return response.status, data


def execute_plan(plan, args, summary):
    if not args.terms_reviewed:
        raise ValueError("Review GSI response conditions before --terms-reviewed")
    if not math.isfinite(args.interval) or args.interval < 1.0:
        raise ValueError("--interval must be at least 1.0 seconds")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    opener = build_opener(NoRedirect())
    counts = Counter()
    attempted = 0
    log_path = args.output / "extra_download_log.sqlite"
    try:
        with log_connection(log_path) as con:
            for row in plan:
                _kind, layer, z, x, y, availability, ext = row
                if availability != "published":
                    counts["unsupported_zoom_skipped"] += 1
                    continue
                target = tile_path(args.download_root, row)
                if target.is_file():
                    if valid_image(target.read_bytes(), ext):
                        counts["already_saved"] += 1
                    else:
                        record(con, row, "invalid_existing", detail="Existing file invalid")
                        counts["invalid_existing"] += 1
                    continue
                if args.limit is not None and attempted >= args.limit:
                    counts["limit_reached"] += 1
                    break
                url = f"{BASE}/{layer}/{z}/{x}/{y}.{ext}"
                try:
                    status, data = fetch(opener, url, args.timeout)
                    if status == 200 and valid_image(data, ext):
                        write_atomic(target, data)
                        record(con, row, "downloaded", status=status, data=data)
                        counts["downloaded"] += 1
                    else:
                        record(con, row, "invalid_response", status=status)
                        counts["invalid_response"] += 1
                except HTTPError as exc:
                    record(con, row, "http_error", status=exc.code, detail=str(exc))
                    counts[f"http_{exc.code}"] += 1
                    if exc.code in {403, 429}:
                        raise RuntimeError(f"GSI returned HTTP {exc.code}; stopped") from exc
                except (OSError, URLError, TimeoutError, ValueError) as exc:
                    record(con, row, "network_error", detail=str(exc))
                    counts["network_error"] += 1
                finally:
                    attempted += 1
                    time.sleep(args.interval)
                if attempted % 20 == 0:
                    print(f"Requested: {attempted}; results: {dict(counts)}", flush=True)
    finally:
        summary["requests_this_run"] = attempted
        summary["results"] = dict(counts)
        summary["reported_at"] = datetime.now(timezone.utc).isoformat()
        (args.output / "extra_download_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def run(args):
    # Do not alter the original M4B summary or sqlite database.
    if args.output.resolve() == args.download_root.resolve():
        raise ValueError("Separate extra output from original downloads directory")
    plan = make_plan(args.area, args.original_log)
    plan_file = write_plan(plan, args.output)
    breakdown = Counter((row[0], row[1], row[2], row[5]) for row in plan)
    summary = {
        "plan_total": len(plan),
        "retry_404": sum(row[0] == "retry_404" for row in plan),
        "extra_zoom": sum(row[0] == "extra_zoom" for row in plan),
        "published_to_request": sum(row[5] == "published" for row in plan),
        "unsupported_zoom_skipped": sum(row[5] == "unsupported_zoom" for row in plan),
        "by_kind_layer_zoom": [
            {"kind": k[0], "layer": k[1], "z": k[2], "availability": k[3], "count": n}
            for k, n in sorted(breakdown.items())
        ],
        "plan_file": str(plan_file),
        "original_log_sha256": hashlib.sha256(args.original_log.read_bytes()).hexdigest(),
        "area_sha256": hashlib.sha256(args.area.read_bytes()).hexdigest(),
        "execute": bool(args.execute),
        "terms_reviewed": bool(args.terms_reviewed),
        "published_source": "https://maps.gsi.go.jp/development/",
    }
    (args.output / "extra_plan_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.execute:
        execute_plan(plan, args, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Supplemental tile plan and 66 retry tool")
    parser.add_argument("--area", required=True, type=Path, help="Approved service_area.geojson")
    parser.add_argument("--original-log", required=True, type=Path)
    parser.add_argument("--download-root", required=True, type=Path,
                        help="Original M4 downloads directory, reused for successful retries")
    parser.add_argument("--output", required=True, type=Path,
                        help="Separate reports directory; not original M4 output")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({k: v for k, v in result.items() if k != "by_kind_layer_zoom"},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
