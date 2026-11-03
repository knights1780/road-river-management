"""M3-C cache tests using self-generated synthetic image data only."""

import sqlite3
from itertools import count

import pytest

from road_river_management.web.map_tile_cache import LocalTileCache

PNG = b"\x89PNG\r\n\x1a\n"


def test_miss_and_hit(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite", max_bytes=100)
    assert cache.get("demo", "r1", 1, 0, 0) is None
    cache.put("demo", "r1", 1, 0, 0, PNG + b"a")
    assert cache.get("demo", "r1", 1, 0, 0) == PNG + b"a"


def test_version_and_layer_isolation(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite")
    cache.put("demo", "r1", 0, 0, 0, PNG + b"a")
    assert cache.get("demo", "r2", 0, 0, 0) is None
    assert cache.get("other", "r1", 0, 0, 0) is None


def test_lru_evicts_oldest(tmp_path, monkeypatch):
    from road_river_management.web import map_tile_cache

    clock = count(start=1)
    monkeypatch.setattr(map_tile_cache.time, "time_ns", lambda: next(clock))
    cache = LocalTileCache(tmp_path / "cache.sqlite", max_bytes=18)
    cache.put("a", "v", 1, 0, 0, PNG + b"a")
    cache.put("a", "v", 1, 1, 0, PNG + b"b")
    assert cache.get("a", "v", 1, 0, 0) == PNG + b"a"
    cache.put("a", "v", 1, 0, 1, PNG + b"c")
    assert cache.get("a", "v", 1, 1, 0) is None
    assert cache.get("a", "v", 1, 0, 0) is not None


def test_oversized_tile_skipped(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite", max_bytes=8)
    cache.put("a", "v", 0, 0, 0, PNG + b"too big")
    assert cache.get("a", "v", 0, 0, 0) is None


def test_invalid_data_rejected(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite")
    with pytest.raises(ValueError):
        cache.put("a", "v", 0, 0, 0, b"not a png")
    with pytest.raises(ValueError):
        cache.get("a", "v", 1, 2, 0)
    with pytest.raises(ValueError):
        LocalTileCache(tmp_path / "bad.sqlite", max_bytes=0)


def test_corrupt_entry_removed(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite")
    with sqlite3.connect(cache.path) as conn:
        conn.execute(
            "INSERT INTO tiles VALUES (?,?,?,?,?,?,?,?)",
            ("a", "v", 0, 0, 0, b"bad", 3, 1),
        )
    assert cache.get("a", "v", 0, 0, 0) is None


def test_clear(tmp_path):
    cache = LocalTileCache(tmp_path / "cache.sqlite")
    cache.put("a", "v", 0, 0, 0, PNG + b"a")
    cache.clear()
    assert cache.get("a", "v", 0, 0, 0) is None
