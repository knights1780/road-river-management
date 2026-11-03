"""Audit downloaded raster tiles and build independent read-only MBTiles releases.

Offline only. Source downloads, the original attempt log and source index remain
unchanged. No guessed tiles, no network calls, no replacement without --replace.
Compatible with Python 3.9+ (GIS virtual environment).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

# Format, native minimum/maximum zoom, approved planned count.
LAYERS = {
    "std": ("png", 5, 18, 4287),
    "pale": ("png", 5, 18, 4287),
    "blank": ("png", 5, 14, 47),
    "seamlessphoto": ("jpg", 14, 18, 4263),
    "lcmfc2": ("png", 11, 16, 341),
    "lcm25k_2012": ("png", 10, 16, 342),
    "swale": ("png", 10, 16, 342),
    "relief": ("png", 5, 15, 114),
}
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
MAX_TILE_BYTES = 5 * 1024 * 1024


def load_index(path: Path, *, strict: bool = True) -> list[tuple[str, int, int, int]]:
    """Read approved original index in XYZ coordinates and validate uniqueness."""
    keys = []
    seen = set()
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not {"layer", "z", "x", "y"}.issubset(reader.fieldnames or []):
            raise ValueError("Index requires layer,z,x,y columns")
        for row in reader:
            layer = row["layer"]
            if layer not in LAYERS:
                raise ValueError("Unknown layer: " + str(layer))
            try:
                z, x, y = (int(row[field]) for field in ("z", "x", "y"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid XYZ coordinates") from exc
            _ext, min_zoom, max_zoom, _expected = LAYERS[layer]
            if not (min_zoom <= z <= max_zoom and 0 <= x < 1 << z and 0 <= y < 1 << z):
                raise ValueError("Out-of-range tile: " + str((layer, z, x, y)))
            key = (layer, z, x, y)
            if key in seen:
                raise ValueError("Duplicate index key: " + str(key))
            seen.add(key)
            keys.append(key)
    if strict:
        actual = Counter(key[0] for key in keys)
        expected = {layer: data[3] for layer, data in LAYERS.items()}
        if dict(actual) != expected:
            raise ValueError("Index differs from approved plan: " + str(dict(actual)))
    return keys


def read_attempts(path: Path) -> dict[tuple[str, int, int, int], tuple[str, int | None, str | None]]:
    """Read-only access to previous downloader's latest outcome per tile."""
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        records = con.execute("SELECT layer,z,x,y,result,status,sha256 FROM attempts")
        return {(layer, z, x, y): (result, status, checksum)
                for layer, z, x, y, result, status, checksum in records}


def image_valid(data: bytes, extension: str) -> bool:
    if not 0 < len(data) <= MAX_TILE_BYTES:
        return False
    if extension == "png":
        return (data.startswith(PNG) and len(data) > 24
                and data[-12:-8] == b"\x00\x00\x00\x00"
                and data[-8:-4] == b"IEND")
    return len(data) > 100 and data.startswith(JPEG) and data.endswith(b"\xff\xd9")


def source_path(root: Path, key: tuple[str, int, int, int]) -> Path:
    layer, z, x, y = key
    return root / layer / str(z) / str(x) / f"{y}.{LAYERS[layer][0]}"


def inspect(
    keys: list[tuple[str, int, int, int]],
    root: Path,
    attempts: dict[tuple[str, int, int, int], tuple[str, int | None, str | None]],
) -> tuple[dict, list[dict]]:
    counts = Counter()
    missing = []
    per_layer = Counter()
    per_zoom = Counter()
    for key in keys:
        layer, z, x, y = key
        ext = LAYERS[layer][0]
        path = source_path(root, key)
        row = attempts.get(key)
        if path.is_file():
            data = path.read_bytes()
            if not image_valid(data, ext):
                raise ValueError(f"Invalid tile format: {path}")
            if row and row[2] and hashlib.sha256(data).hexdigest() != row[2]:
                raise ValueError(f"SHA256 mismatch: {path}")
            counts["present"] += 1
            per_layer[layer] += 1
            per_zoom[(layer, z)] += 1
        else:
            if not row or row[0] != "http_error" or row[1] != 404:
                raise ValueError(f"Unexplained missing tile (not logged as HTTP 404): {key}")
            counts["missing_404"] += 1
            missing.append({"layer": layer, "z": z, "x": x, "y": y,
                            "status": 404, "reason": "source_http_404"})
    summary = {
        "planned": len(keys), "present": counts["present"],
        "missing_404": counts["missing_404"],
        "present_by_layer": {layer: per_layer[layer] for layer in LAYERS},
        "present_by_layer_zoom": [
            {"layer": layer, "z": z, "count": count}
            for (layer, z), count in sorted(per_zoom.items())
        ],
        "missing_by_layer": dict(sorted(Counter(r["layer"] for r in missing).items())),
        "fully_covered": not missing,
    }
    return summary, missing


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_audit(output: Path, summary: dict, missing: list[dict], *, index: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    report = {**summary, "index_sha256": file_sha256(index),
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "missing_tiles_csv": "missing_tiles.csv"}
    (output / "audit_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (output / "missing_tiles.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["layer", "z", "x", "y", "status", "reason"])
        writer.writeheader()
        writer.writerows(missing)


def _make_mbtiles(path: Path, layer: str, layer_keys, root: Path) -> tuple[int, int]:
    ext, min_z, max_z, _ = LAYERS[layer]
    with closing(sqlite3.connect(path)) as con:
        con.executescript("""
            CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tiles (
                zoom_level INTEGER NOT NULL,
                tile_column INTEGER NOT NULL,
                tile_row INTEGER NOT NULL,
                tile_data BLOB NOT NULL,
                PRIMARY KEY (zoom_level,tile_column,tile_row)
            );
        """)
        con.executemany("INSERT INTO metadata VALUES (?, ?)", [
            ("name", f"GSI {layer} - offline municipal map"),
            ("format", "jpg" if ext == "jpg" else "png"),
            ("type", "baselayer"),
            ("version", "1.0"),
            ("minzoom", str(min_z)),
            ("maxzoom", str(max_z)),
            ("description", "Offline copy of raster tiles; see separate acquisition records"),
        ])
        inserted = 0
        total_bytes = 0
        for layer_name, z, x, y in layer_keys:
            key = (layer_name, z, x, y)
            path_in = source_path(root, key)
            if not path_in.is_file():
                continue
            data = path_in.read_bytes()
            if not image_valid(data, ext):
                raise ValueError(f"Tile changed during build: {path_in}")
            con.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                        (z, x, (1 << z) - 1 - y, data))
            inserted += 1
            total_bytes += len(data)
        con.commit()
        verified = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        if verified != inserted:
            raise ValueError("Unexpected MBTiles row count")
    return inserted, total_bytes


def build_mbtiles(
    output: Path, keys: list[tuple[str, int, int, int]], root: Path,
    audit: dict, missing: list[dict], *, replace: bool = False,
) -> dict:
    """Stage 8 MBTiles, verify counts, then publish only after all succeed."""
    output.mkdir(parents=True, exist_ok=True)
    targets = {layer: output / f"{layer}.mbtiles" for layer in LAYERS}
    staged = {layer: output / f".{layer}.mbtiles.creating" for layer in LAYERS}
    for layer in LAYERS:
        if targets[layer].exists() and not replace:
            raise FileExistsError(f"Existing MBTiles: {targets[layer]}")
        if staged[layer].exists():
            raise FileExistsError(f"Old temporary MBTiles exists: {staged[layer]}")
    entries = []
    try:
        for layer in LAYERS:
            group = [key for key in keys if key[0] == layer]
            written, payload_bytes = _make_mbtiles(staged[layer], layer, group, root)
            if written != audit["present_by_layer"][layer]:
                raise ValueError(f"Tile count changed for {layer}; re-run audit")
            entries.append({
                "layer": layer, "filename": f"{layer}.mbtiles",
                "format": LAYERS[layer][0], "min_native_zoom": LAYERS[layer][1],
                "max_native_zoom": LAYERS[layer][2], "planned": len(group),
                "tiles": written, "missing": len(group) - written,
                "payload_bytes": payload_bytes,
            })
        if sum(item["tiles"] for item in entries) != audit["present"]:
            raise ValueError("Total tile count mismatch")
        # Commit on the same filesystem only after every staged database is ready.
        for layer in LAYERS:
            os.replace(staged[layer], targets[layer])
        for item in entries:
            path = output / item["filename"]
            item["file_bytes"] = path.stat().st_size
            item["sha256"] = file_sha256(path)
        manifest = {
            "status": "staging_not_released", "layers": entries,
            "planned": len(keys), "tiles": audit["present"],
            "missing": len(missing),
            "note": "Review missing_tiles.csv and validation before production release",
        }
        (output / "conversion_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return manifest
    finally:
        for path in staged.values():
            if path.exists():
                path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline GSI tile audit and MBTiles builder")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build", action="store_true", help="Create MBTiles after audit")
    parser.add_argument("--replace", action="store_true", help="Overwrite existing MBTiles on rebuild")
    args = parser.parse_args()
    keys = load_index(args.index)
    attempts = read_attempts(args.log)
    summary, missing = inspect(keys, args.downloads, attempts)
    write_audit(args.output, summary, missing, index=args.index)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.build:
        result = build_mbtiles(args.output, keys, args.downloads, summary, missing,
                               replace=args.replace)
        print(f"Staging MBTiles built: {result['tiles']} tiles across 8 layers", flush=True)


if __name__ == "__main__":
    main()
