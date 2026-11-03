"""Offline MBTiles storage for local demonstration tiles only.

No HTTP access. SQLite MBTiles stores TMS tile_row, while Leaflet uses XYZ y.
This module is deliberately independent of the operational municipality database.
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_xyz(z: int, x: int, y: int) -> None:
    """Reject invalid XYZ indices and non-integral values (including bool)."""
    if any(type(v) is not int for v in (z, x, y)):
        raise TypeError("z, x and y must be integers")
    if not 0 <= z <= 22:
        raise ValueError("z must be between 0 and 22")
    upper = 1 << z
    if not (0 <= x < upper and 0 <= y < upper):
        raise ValueError("x and y must be in the range [0, 2**z)")


def xyz_to_tms_row(z: int, y: int) -> int:
    validate_xyz(z, 0, y)
    return (1 << z) - 1 - y


def _chunk(name: bytes, data: bytes) -> bytes:
    payload = name + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def solid_png(*, r: int = 184, g: int = 217, b: int = 237) -> bytes:
    """Generate an original 256x256 RGB test tile using Python's standard library."""
    if any(type(c) is not int or not 0 <= c <= 255 for c in (r, g, b)):
        raise ValueError("RGB channels must be integers from 0 through 255")
    row = b"\x00" + bytes((r, g, b)) * 256
    raw = row * 256
    header = struct.pack(">2I5B", 256, 256, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, level=6))
        + _chunk(b"IEND", b"")
    )


def _check_tile_data(data: bytes) -> None:
    if not isinstance(data, bytes):
        raise TypeError("tile data must be bytes")
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("only PNG tiles are accepted in the M3-A demo")


def create_demo_mbtiles(path: str | Path, *, overwrite: bool = False) -> Path:
    """Create a small, self-produced MBTiles file; never downloads external tiles.

    Protect an existing file unless overwrite=True. When overwrite is requested,
    write a fresh temporary database and atomically replace the target on success.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing MBTiles: {target}")
    temp = target.with_name(target.name + ".creating")
    if temp.exists():
        raise FileExistsError(f"Temporary MBTiles already exists: {temp}")
    try:
        with sqlite3.connect(temp) as conn:
            conn.executescript(
                """
                CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tiles (
                    zoom_level INTEGER NOT NULL,
                    tile_column INTEGER NOT NULL,
                    tile_row INTEGER NOT NULL,
                    tile_data BLOB NOT NULL,
                    PRIMARY KEY (zoom_level, tile_column, tile_row)
                );
                """
            )
            conn.executemany(
                "INSERT INTO metadata (name, value) VALUES (?, ?)",
                [
                    ("name", "Road River Management original demo tiles"),
                    ("type", "baselayer"),
                    ("version", "1.0"),
                    ("description", "Synthetic local test image; not GSI map data"),
                    ("format", "png"),
                    ("minzoom", "0"),
                    ("maxzoom", "1"),
                    ("bounds", "-180,-85,180,85"),
                ],
            )
            colors = [
                (0, 0, 0, 184, 217, 237),
                (1, 0, 0, 140, 199, 187),
                (1, 1, 0, 242, 218, 154),
                (1, 0, 1, 204, 194, 223),
                (1, 1, 1, 238, 193, 183),
            ]
            for z, x, y, r, g, b in colors:
                conn.execute(
                    "INSERT INTO tiles VALUES (?, ?, ?, ?)",
                    (z, x, xyz_to_tms_row(z, y), solid_png(r=r, g=g, b=b)),
                )
            conn.commit()
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


@contextmanager
def open_readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open existing MBTiles read-only; never create a database on read."""
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise ValueError(f"MBTiles must be a regular file: {target}")
    uri = target.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def read_metadata(path: str | Path) -> dict[str, str]:
    with open_readonly(path) as conn:
        return dict(conn.execute("SELECT name, value FROM metadata"))


def read_tile(path: str | Path, z: int, x: int, y: int) -> bytes | None:
    """Read an XYZ tile from MBTiles; return None if not in file."""
    validate_xyz(z, x, y)
    with open_readonly(path) as conn:
        result = conn.execute(
            """SELECT tile_data FROM tiles
               WHERE zoom_level=? AND tile_column=? AND tile_row=?""",
            (z, x, xyz_to_tms_row(z, y)),
        ).fetchone()
        return result[0] if result else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline MBTiles demonstrator")
    parser.add_argument("--create-demo", type=Path, help="Create original demo MBTiles")
    parser.add_argument("--inspect", type=Path, help="Print MBTiles metadata")
    parser.add_argument("--overwrite", action="store_true", help="Replace demo only when explicitly requested")
    args = parser.parse_args()
    if bool(args.create_demo) == bool(args.inspect):
        parser.error("Specify exactly one of --create-demo and --inspect")
    if args.create_demo:
        output = create_demo_mbtiles(args.create_demo, overwrite=args.overwrite)
        print(f"Demo MBTiles created: {output} ({output.stat().st_size:,} bytes)")
    else:
        for key, value in sorted(read_metadata(args.inspect).items()):
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
