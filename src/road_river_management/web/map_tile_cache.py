"""Size-bounded local SQLite cache for immutable offline map tile releases.

The cache database belongs on the workstation, never on the shared NAS.
Each release must have a new explicit version when its tile content changes.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n"
DEFAULT_LIMIT_BYTES = 2 * 1024**3


class LocalTileCache:
    """Local best-effort LRU cache. A cache fault must not overwrite source data."""

    def __init__(self, path: str | Path, *, max_bytes: int = DEFAULT_LIMIT_BYTES):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.path = Path(path).expanduser().resolve()
        self.max_bytes = max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS tiles (
                layer TEXT NOT NULL,
                version TEXT NOT NULL,
                z INTEGER NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                data BLOB NOT NULL,
                size_bytes INTEGER NOT NULL,
                accessed_ns INTEGER NOT NULL,
                PRIMARY KEY (layer, version, z, x, y)
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS tiles_lru
                ON tiles (accessed_ns)""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _validate_key(layer: str, version: str, z: int, x: int, y: int) -> None:
        if not layer or not version or len(layer) > 100 or len(version) > 100:
            raise ValueError("layer and version must be nonempty and at most 100 chars")
        if any(type(i) is not int for i in (z, x, y)) or not 0 <= z <= 22:
            raise ValueError("invalid tile indices")
        if not (0 <= x < 1 << z and 0 <= y < 1 << z):
            raise ValueError("invalid tile indices")

    def get(self, layer: str, version: str, z: int, x: int, y: int) -> bytes | None:
        self._validate_key(layer, version, z, x, y)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT data FROM tiles
                   WHERE layer=? AND version=? AND z=? AND x=? AND y=?""",
                (layer, version, z, x, y),
            ).fetchone()
            if row is None:
                return None
            data = row[0]
            if not isinstance(data, bytes) or not data.startswith(PNG):
                conn.execute(
                    "DELETE FROM tiles WHERE layer=? AND version=? AND z=? AND x=? AND y=?",
                    (layer, version, z, x, y),
                )
                return None
            conn.execute(
                """UPDATE tiles SET accessed_ns=?
                   WHERE layer=? AND version=? AND z=? AND x=? AND y=?""",
                (time.time_ns(), layer, version, z, x, y),
            )
            return data

    def put(self, layer: str, version: str, z: int, x: int, y: int, data: bytes) -> None:
        self._validate_key(layer, version, z, x, y)
        if not isinstance(data, bytes) or not data.startswith(PNG):
            raise ValueError("only PNG tile data may be cached")
        if len(data) > self.max_bytes:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO tiles (layer, version, z, x, y, data, size_bytes, accessed_ns)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(layer, version, z, x, y) DO UPDATE SET
                     data=excluded.data, size_bytes=excluded.size_bytes,
                     accessed_ns=excluded.accessed_ns""",
                (layer, version, z, x, y, data, len(data), time.time_ns()),
            )
            total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM tiles").fetchone()[0]
            while total > self.max_bytes:
                oldest = conn.execute(
                    "SELECT layer, version, z, x, y, size_bytes FROM tiles "
                    "ORDER BY accessed_ns, rowid LIMIT 1"
                ).fetchone()
                if oldest is None:
                    break
                conn.execute(
                    "DELETE FROM tiles WHERE layer=? AND version=? AND z=? AND x=? AND y=?",
                    oldest[:5],
                )
                total -= oldest[5]

    def clear(self) -> None:
        """Clear only the workstation cache, never the NAS map data."""
        with self._connect() as conn:
            conn.execute("DELETE FROM tiles")
