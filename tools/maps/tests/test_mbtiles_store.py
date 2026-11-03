"""M3-A tests: no external HTTP or GSI tile usage."""

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from mbtiles_store import (
    PNG_SIGNATURE,
    create_demo_mbtiles,
    open_readonly,
    read_metadata,
    read_tile,
    solid_png,
    validate_xyz,
    xyz_to_tms_row,
)


def test_xyz_tms_coordinate_conversion():
    assert xyz_to_tms_row(0, 0) == 0
    assert xyz_to_tms_row(1, 0) == 1
    assert xyz_to_tms_row(1, 1) == 0
    assert xyz_to_tms_row(18, 12345) == 262143 - 12345


@pytest.mark.parametrize("xyz", [(1, 2, 0), (1, 0, -1), (-1, 0, 0), (23, 0, 0)])
def test_reject_out_of_bounds(xyz):
    with pytest.raises(ValueError):
        validate_xyz(*xyz)


def test_reject_wrong_types():
    with pytest.raises(TypeError):
        validate_xyz(1, True, 0)


def test_png_is_valid_structure():
    png = solid_png()
    assert png.startswith(PNG_SIGNATURE)
    assert b"IHDR" in png and b"IEND" in png


def test_demo_roundtrip_and_metadata(tmp_path):
    path = tmp_path / "demo.mbtiles"
    create_demo_mbtiles(path)
    assert read_metadata(path)["format"] == "png"
    assert read_tile(path, 1, 0, 0) == solid_png(r=140, g=199, b=187)
    assert read_tile(path, 1, 0, 1) == solid_png(r=204, g=194, b=223)
    assert read_tile(path, 2, 0, 0) is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 5
        assert conn.execute(
            "SELECT tile_row FROM tiles WHERE zoom_level=1 AND tile_column=0 AND tile_data=?",
            (solid_png(r=140, g=199, b=187),),
        ).fetchone()[0] == 1


def test_readonly_does_not_change_file(tmp_path):
    path = tmp_path / "demo.mbtiles"
    create_demo_mbtiles(path)
    before = path.read_bytes()
    with open_readonly(path) as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM tiles")
    assert path.read_bytes() == before


def test_existing_file_not_overwritten(tmp_path):
    path = tmp_path / "demo.mbtiles"
    create_demo_mbtiles(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        create_demo_mbtiles(path)
    assert path.read_bytes() == before


def test_explicit_overwrite(tmp_path):
    path = tmp_path / "demo.mbtiles"
    create_demo_mbtiles(path)
    create_demo_mbtiles(path, overwrite=True)
    assert read_tile(path, 0, 0, 0) is not None


def test_read_missing_file_never_creates_it(tmp_path):
    path = tmp_path / "absent.mbtiles"
    with pytest.raises(FileNotFoundError):
        read_tile(path, 0, 0, 0)
    assert not path.exists()
