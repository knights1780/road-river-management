#!/usr/bin/env python3
"""Create the Yoshida offline-map AOI from actual public GIS boundary files.

No map tile downloads or internet requests are made by this script.
Requirements: geopandas, shapely, pyproj, fiona.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union
from shapely.prepared import prep


CITY_COLS = ("N03_004", "CITY_NAME", "CITY", "city_name", "市区町村名", "市区町村")
AREA_COLS = ("S_NAME", "AREA_NAME", "OAZA_NAME", "大字・町名", "町丁字", "町丁・字等名称", "MOJI", "name")
CODE_COLS = ("N03_007", "CITY_CODE", "city_code", "市区町村コード")


def _column(frame, names, label):
    for name in names:
        if name in frame.columns:
            return name
    raise ValueError(f"{label} column not found. Available: {list(frame.columns)}")


def _read_gis(path: Path) -> gpd.GeoDataFrame:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".zip":
        return gpd.read_file(path)
    # GeoPandas/GDAL can read shapefiles directly from ZIPs on many installations.
    try:
        return gpd.read_file(f"zip://{path}")
    except Exception:
        pass
    with tempfile.TemporaryDirectory(prefix="rrm-gis-") as temp:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                target = (Path(temp) / entry.filename).resolve()
                if not target.is_relative_to(Path(temp).resolve()):
                    raise ValueError("Unsafe filename in ZIP")
            archive.extractall(temp)
        candidates = sorted(Path(temp).rglob("*.shp"))
        if not candidates:
            candidates = sorted(Path(temp).rglob("*.geojson"))
        if len(candidates) != 1:
            raise ValueError(f"ZIP must contain a single GIS layer; found {candidates}")
        return gpd.read_file(candidates[0])


def _normalize(s):
    return "".join(str(s).replace("　", " ").split()).replace("ヶ", "ケ")


def _valid_union(geometries):
    parts = [g for g in geometries if g is not None and not g.is_empty]
    if not parts:
        raise ValueError("Empty geometry selection")
    result = unary_union(parts)
    if not result.is_valid:
        result = result.buffer(0)
    if result.is_empty:
        raise ValueError("Geometry became empty")
    return result


def _geometry_feature(geom, **properties):
    return {"type": "Feature", "properties": properties, "geometry": mapping(geom)}


def create_area(municipal, small_areas, config):
    if municipal.crs is None or small_areas.crs is None:
        raise ValueError("Input GIS layers must have a declared CRS")
    municipal = municipal.to_crs("EPSG:4326")
    small_areas = small_areas.to_crs("EPSG:4326")
    municipal_city = _column(municipal, CITY_COLS, "municipal city")
    small_city = _column(small_areas, CITY_COLS, "small-area city")
    small_name = _column(small_areas, AREA_COLS, "small-area name")
    town = config["base_municipality"]["name"]
    yg = municipal[municipal[municipal_city].astype(str) == town]
    if yg.empty:
        raise ValueError(f"Missing {town} in municipality boundaries")
    yoshida = _valid_union(yg.geometry)
    # Prevent small-area dataset from extending beyond municipal boundaries.
    city_geom = {}
    features = [_geometry_feature(yoshida, zone_id="yoshida", name="吉田町全域", status="source_boundary")]
    selected = [yoshida]
    audits = {}
    for group in config["districts"]:
        city = group["municipality"]
        if city not in city_geom:
            matches = municipal[municipal[municipal_city].astype(str) == city]
            if matches.empty:
                raise ValueError(f"Missing municipality: {city}")
            city_geom[city] = _valid_union(matches.geometry)
        rows = small_areas[small_areas[small_city].astype(str) == city]
        prefix = [_normalize(t) for t in group["name_prefixes"]]
        names = rows[small_name].fillna("").astype(str).map(_normalize)
        included = rows[names.map(lambda s: any(s == p or s.startswith(p) for p in prefix))]
        found = sorted(set(included[small_name].fillna("").astype(str)))
        missing = [p for p in prefix if not any(_normalize(n).startswith(p) for n in found)]
        if included.empty:
            raise ValueError(f"No matching small areas: {group['id']}")
        district = _valid_union(included.geometry).intersection(city_geom[city])
        if district.is_empty:
            raise ValueError(f"Empty district after clipping: {group['id']}")
        selected.append(district)
        features.append(_geometry_feature(district, zone_id=group["id"], name=group["name"], status="draft_review_required"))
        audits[group["id"]] = {"matched_names": found, "unmatched_prefixes": missing, "selected_rows": len(included)}
    to_m = Transformer.from_crs("EPSG:4326", config["projection_for_buffer"], always_xy=True).transform
    to_ll = Transformer.from_crs(config["projection_for_buffer"], "EPSG:4326", always_xy=True).transform
    yoshida_m = transform(to_m, yoshida)
    all_land = _valid_union(municipal.geometry)
    all_land_m = transform(to_m, all_land)
    # Only the part of the 1-km coastal buffer that is not mapped as land.
    # Because Yoshida is the base, this does not expand other cities out to sea.
    sea_m = yoshida_m.buffer(float(config["sea_buffer_m"])).difference(all_land_m)
    if not sea_m.is_empty:
        sea = transform(to_ll, sea_m)
        features.append(_geometry_feature(sea, zone_id="coastal_sea", name="吉田町沿岸の海上約1km", status="derived_buffer"))
        selected.append(sea)
    whole = _valid_union(selected)
    return whole, features, audits


def _lon_x(lon, z):
    return max(0, min(2**z - 1, math.floor((lon + 180) / 360 * 2**z)))


def _lat_y(lat, z):
    lat = max(-85.0511287798066, min(85.0511287798066, lat))
    lat_rad = math.radians(lat)
    return max(0, min(2**z - 1, math.floor((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * 2**z)))


def _tile_bounds(z, x, y):
    n = 2**z
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    west, east = x / n * 360 - 180, (x + 1) / n * 360 - 180
    return box(west, south, east, north)


def enumerate_tiles(geometry, z):
    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    prepared = prep(geometry)
    for x in range(_lon_x(min_lon, z), _lon_x(max_lon, z) + 1):
        for y in range(_lat_y(max_lat, z), _lat_y(min_lat, z) + 1):
            if prepared.intersects(_tile_bounds(z, x, y)):
                yield (z, x, y)


def build_outputs(area, features, audits, config, output, include_tile_index):
    output.mkdir(parents=True, exist_ok=True)
    (output / "service_area.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [_geometry_feature(area,
            project="road-river-management", area="吉田町＋初倉＋大井川＋榛原東部＋沿岸海上",
            status="DRAFT_REVIEW_REQUIRED", crs="EPSG:4326")]
    }, ensure_ascii=False), encoding="utf-8")
    (output / "area_zones.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": features
    }, ensure_ascii=False), encoding="utf-8")
    tile_limits = sorted(set((z for layer in config["tile_layers"]
                              for z in range(layer["min_zoom"], layer["max_zoom"] + 1))))
    counts = {}
    tiles = defaultdict(list)
    for z in tile_limits:
        tiles[z] = list(enumerate_tiles(area, z))
        counts[z] = len(tiles[z])
    rows = []
    for layer in config["tile_layers"]:
        count = sum(counts[z] for z in range(layer["min_zoom"], layer["max_zoom"] + 1))
        rows.append({"layer": layer["id"], "name": layer["name"],
                     "z_min": layer["min_zoom"], "z_max": layer["max_zoom"],
                     "tile_count": count,
                     "low_mb": round(count * layer["average_kb_low"] / 1024, 1),
                     "high_mb": round(count * layer["average_kb_high"] / 1024, 1)})
    with (output / "tile_budget.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if include_tile_index:
        with gzip.open(output / "tile_index.csv.gz", "wt", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("layer", "z", "x", "y"))
            for layer in config["tile_layers"]:
                for z in range(layer["min_zoom"], layer["max_zoom"] + 1):
                    for _, x, y in tiles[z]:
                        writer.writerow((layer["id"], z, x, y))
    trans = Transformer.from_crs("EPSG:4326", config["projection_for_buffer"], always_xy=True).transform
    area_km2 = transform(trans, area).area / 1e6
    report = {
        "status": "DRAFT_REVIEW_REQUIRED", "source": config["municipality_source"],
        "small_area_source": config["small_area_source"],
        "bounds_lon_lat": list(area.bounds), "area_km2": round(area_km2, 3),
        "sea_buffer_m": config["sea_buffer_m"], "audit": audits,
        "tile_counts_by_zoom": counts, "layers": rows,
        "total_tile_requests": sum(r["tile_count"] for r in rows),
        "storage_mb_low": round(sum(r["low_mb"] for r in rows), 1),
        "storage_mb_high": round(sum(r["high_mb"] for r in rows), 1),
        "note": "Raw tile-count upper bound assuming tiles exist at all selected zooms. Not a permissions or availability check."
    }
    (output / "tile_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser(description="吉田町周辺地図の取得範囲GeoJSON・タイル数を作成（タイルはダウンロードしません）")
    p.add_argument("--municipal", type=Path, required=True, help="N03静岡県のZIP/SHP/GeoJSON")
    p.add_argument("--smallarea", type=Path, required=True, help="e-Stat小地域境界のZIP/SHP/GeoJSON")
    p.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config/area_selection.json")
    p.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "output")
    p.add_argument("--skip-tile-index", action="store_true", help="容量と個数のみ出力")
    args = p.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    area, features, audits = create_area(_read_gis(args.municipal), _read_gis(args.smallarea), config)
    report = build_outputs(area, features, audits, config, args.output, not args.skip_tile_index)
    print(f"Area: {report['area_km2']:.2f} km²; all-layer tiles: {report['total_tile_requests']:,}")
    print(f"Storage estimate: {report['storage_mb_low']:.0f}–{report['storage_mb_high']:.0f} MiB")
    print(f"Output: {args.output.resolve()}; status: {report['status']}")
    for key, audit in audits.items():
        print(key, "matched:", ", ".join(audit["matched_names"]))
        if audit["unmatched_prefixes"]:
            print("  REVIEW missing:", ", ".join(audit["unmatched_prefixes"]))


if __name__ == "__main__":
    main()
