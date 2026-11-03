"""Offline synthetic tests for M4-C MBTiles packaging."""

import csv
import gzip
import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_gsi_mbtiles.py"
spec = importlib.util.spec_from_file_location("build_gsi_mbtiles", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def make_png():
    import struct
    import zlib

    def chunk(kind, body):
        data = kind + body
        return struct.pack(">I", len(body)) + data + struct.pack(">I", zlib.crc32(data))

    return (mod.PNG + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def fixture(tmp_path):
    index = tmp_path / "index.csv.gz"
    rows = [("std", 5, 28, 12), ("std", 5, 28, 13), ("lcmfc2", 11, 1810, 813)]
    with gzip.open(index, "wt", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["layer", "z", "x", "y"])
        writer.writerows(rows)
    root = tmp_path / "downloads"
    for key in rows[:2]:
        p = mod.source_path(root, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_png())
    log = tmp_path / "attempts.sqlite"
    with sqlite3.connect(log) as con:
        con.execute("CREATE TABLE attempts (layer,z,x,y,result,status,sha256)")
        con.execute("INSERT INTO attempts VALUES ('lcmfc2',11,1810,813,'http_error',404,NULL)")
    return index, root, log, rows


def test_index_and_missing(tmp_path):
    index, root, log, keys = fixture(tmp_path)
    assert mod.load_index(index, strict=False) == keys
    summary, missing = mod.inspect(keys, root, mod.read_attempts(log))
    assert (summary["present"], summary["missing_404"]) == (2, 1)
    assert missing[0]["layer"] == "lcmfc2"


def test_requires_approved_count(tmp_path):
    index, _, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="approved plan"):
        mod.load_index(index)


def test_unexplained_missing_is_error(tmp_path):
    _index, root, _, keys = fixture(tmp_path)
    with pytest.raises(ValueError, match="Unexplained"):
        mod.inspect(keys, root, {})


def test_format_failure(tmp_path):
    _index, root, log, keys = fixture(tmp_path)
    mod.source_path(root, keys[0]).write_bytes(b"bad")
    with pytest.raises(ValueError, match="Invalid tile"):
        mod.inspect(keys, root, mod.read_attempts(log))


def test_conversion_tms_and_count(tmp_path, monkeypatch):
    _, root, log, keys = fixture(tmp_path)
    # Use a miniature layer set only in this isolated test.
    monkeypatch.setattr(mod, "LAYERS", {"std": ("png", 5, 5, 2),
                                       "lcmfc2": ("png", 11, 11, 1)})
    summary, missing = mod.inspect(keys, root, mod.read_attempts(log))
    result = mod.build_mbtiles(tmp_path / "out", keys, root, summary, missing)
    assert (result["tiles"], result["missing"]) == (2, 1)
    with sqlite3.connect(tmp_path / "out" / "std.mbtiles") as con:
        rows = con.execute("SELECT zoom_level,tile_column,tile_row FROM tiles ORDER BY tile_row").fetchall()
    assert rows == [(5, 28, 18), (5, 28, 19)]
    with pytest.raises(FileExistsError):
        mod.build_mbtiles(tmp_path / "out", keys, root, summary, missing)


def test_sha_mismatch(tmp_path):
    _, root, log, keys = fixture(tmp_path)
    with sqlite3.connect(log) as con:
        con.execute("INSERT INTO attempts VALUES ('std',5,28,12,'downloaded',200,'bad-hash')")
    with pytest.raises(ValueError, match="SHA256"):
        mod.inspect(keys, root, mod.read_attempts(log))


def test_audit_files(tmp_path):
    index, root, log, keys = fixture(tmp_path)
    summary, missing = mod.inspect(keys, root, mod.read_attempts(log))
    mod.write_audit(tmp_path / "out", summary, missing, index=index)
    assert (tmp_path / "out" / "missing_tiles.csv").read_text().count("lcmfc2") == 1
    assert (tmp_path / "out" / "audit_summary.json").exists()
