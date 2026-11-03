"""M3-C integration tests: login and source-read separation."""

import sqlite3

import pytest

from road_river_management.database import migrate
from road_river_management.web.app import create_app, create_local_user

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def environment(tmp_path):
    db = tmp_path / "operations.sqlite"
    conn = migrate.get_connection(db)
    migrate.ensure_schema_versions(conn)
    migrate.upgrade(conn)
    conn.close()
    mb = tmp_path / "demo.mbtiles"
    with sqlite3.connect(mb) as conn:
        conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
        conn.execute("INSERT INTO tiles VALUES (0,0,0,?)", (PNG + b"tile",))
    create_local_user(db, login_id="tester", password="test-password", display_name="架空", role_code="VIEWER")
    return db, mb, tmp_path / "cache" / "tiles.sqlite"


def _client(db, mb, cache, version="release1"):
    app = create_app(database_path=db, secret_key="x" * 48, map_sources={"demo": mb},
                     map_cache_path=cache, map_versions={"demo": version})
    app.config["TESTING"] = True
    return app.test_client()


def _login(client):
    client.get("/login")
    with client.session_transaction() as session:
        token = session["login_csrf"]
    assert client.post("/login", data={"login_id": "tester", "password": "test-password", "csrf_token": token}).status_code == 302


def test_cached_tile_survives_nas_disconnection(environment):
    db, mb, cache = environment
    client = _client(db, mb, cache)
    url = "/api/v1/maps/demo/0/0/0.png"
    assert client.get(url).status_code == 403
    _login(client)
    assert client.get(url).data == PNG + b"tile"
    mb.unlink()
    assert client.get(url).data == PNG + b"tile"
    assert client.get(url).headers["Cache-Control"] == "no-store"


def test_new_version_does_not_reuse_old_cache(environment):
    db, mb, cache = environment
    client = _client(db, mb, cache)
    _login(client)
    url = "/api/v1/maps/demo/0/0/0.png"
    assert client.get(url).status_code == 200
    mb.unlink()
    client2 = _client(db, mb, cache, version="release2")
    _login(client2)
    assert client2.get(url).status_code == 503


def test_locked_user_cannot_read_cached_tile(environment):
    db, mb, cache = environment
    client = _client(db, mb, cache)
    _login(client)
    url = "/api/v1/maps/demo/0/0/0.png"
    assert client.get(url).status_code == 200
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE users SET is_locked=1 WHERE login_id='tester'")
    assert client.get(url).status_code == 403


def test_without_release_version_cache_is_bypassed(environment):
    db, mb, cache = environment
    app = create_app(database_path=db, secret_key="x" * 48, map_sources={"demo": mb},
                     map_cache_path=cache)
    client = app.test_client()
    _login(client)
    url = "/api/v1/maps/demo/0/0/0.png"
    assert client.get(url).status_code == 200
    mb.unlink()
    assert client.get(url).status_code == 503
