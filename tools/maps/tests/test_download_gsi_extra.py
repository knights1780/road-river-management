"""Offline-only tests of M4 supplemental tile plan and retry behavior."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from shapely.geometry import box

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "download_gsi_extra.py"
spec = importlib.util.spec_from_file_location("download_gsi_extra", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_retries_read_exact_66(tmp_path):
    log = tmp_path / "original.sqlite"
    with sqlite3.connect(log) as con:
        con.execute("CREATE TABLE attempts (layer,z,x,y,result,status)")
        con.executemany("INSERT INTO attempts VALUES (?,?,?,?,?,?)", [
            ("lcmfc2", 16, 500 + n, 100, "http_error", 404) for n in range(55)
        ] + [
            ("lcm25k_2012", 15, 200 + n, 100, "http_error", 404) for n in range(10)
        ] + [("swale", 16, 600, 100, "http_error", 404)])
    assert len(module.retry_keys(log)) == 66


def test_bad_retry_count_fails(tmp_path):
    log = tmp_path / "original.sqlite"
    with sqlite3.connect(log) as con:
        con.execute("CREATE TABLE attempts (layer,z,x,y,result,status)")
    with pytest.raises(ValueError, match="exactly 66"):
        module.retry_keys(log)


def test_tile_selection_for_area():
    area = box(138.25, 34.75, 138.26, 34.76)
    tiles = list(module.tiles_for_zoom(area, 12))
    assert tiles
    for x, y in tiles:
        assert area.intersects(module.tile_box(12, x, y))


def test_published_ranges_flag_unsupported():
    for layer, ranges in module.EXTRA_RANGES.items():
        _, low, high = module.PUBLISHED[layer]
        assert all(z < low or z > high for start, end in ranges for z in range(start, end + 1))


def test_read_area(tmp_path):
    path = tmp_path / "area.geojson"
    path.write_text(json.dumps({"type": "Feature", "geometry": box(138, 34, 139, 35).__geo_interface__, "properties": {}}))
    assert not module.read_area(path).is_empty


def test_invalid_image():
    assert not module.valid_image(b"hi", "png")
    assert not module.valid_image(b"hi", "jpg")
