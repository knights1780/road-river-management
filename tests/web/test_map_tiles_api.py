"""M3-B offline tile serving tests with locally generated synthetic tiles."""

import sqlite3

import pytest

from road_river_management.database import migrate
from road_river_management.web.app import create_app, create_local_user

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "operational.sqlite"
    conn = migrate.get_connection(path)
    migrate.ensure_schema_versions(conn)
    migrate.upgrade(conn)
    conn.close()
    return path


@pytest.fixture
def tile_path(tmp_path):
    path = tmp_path / "demo.mbtiles"
    with sqlite3.connect(path) as con:
        con.executescript(
            """CREATE TABLE tiles (
                zoom_level INTEGER, tile_column INTEGER,
                tile_row INTEGER, tile_data BLOB,
                PRIMARY KEY (zoom_level, tile_column, tile_row));"""
        )
        con.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (1, 0, 1, PNG + b"demo"))
    return path


@pytest.fixture
def client(db_path, tile_path):
    app = create_app(
        database_path=db_path,
        secret_key="s" * 48,
        map_sources={"demo": tile_path},
    )
    app.config["TESTING"] = True
    return app.test_client()


def _login(client, db_path, *, role="VIEWER"):
    create_local_user(
        db_path, login_id="tester", password="test-password",
        display_name="架空ユーザー", role_code=role,
    )
    client.get("/login")
    with client.session_transaction() as sess:
        csrf = sess["login_csrf"]
    resp = client.post(
        "/login",
        data={"login_id": "tester", "password": "test-password", "csrf_token": csrf},
    )
    assert resp.status_code == 302


def test_denied_without_login(client):
    assert client.get("/api/v1/maps/demo/1/0/0.png").status_code == 403


def test_viewer_can_read_png(client, db_path):
    _login(client, db_path)
    response = client.get("/api/v1/maps/demo/1/0/0.png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data == PNG + b"demo"
    assert response.headers["Cache-Control"] == "no-store"  # App's global policy
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_unknown_layer_blocked(client, db_path):
    _login(client, db_path)
    assert client.get("/api/v1/maps/secrets/1/0/0.png").status_code == 404


@pytest.mark.parametrize("url", [
    "/api/v1/maps/demo/1/2/0.png",
    "/api/v1/maps/demo/1/0/2.png",
    "/api/v1/maps/demo/23/0/0.png",
])
def test_invalid_coordinates(client, db_path, url):
    _login(client, db_path)
    assert client.get(url).status_code == 400


def test_missing_tile_is_404(client, db_path):
    _login(client, db_path)
    assert client.get("/api/v1/maps/demo/1/1/1.png").status_code == 404


def test_missing_source_is_503(client, db_path, tile_path):
    _login(client, db_path)
    tile_path.unlink()
    assert client.get("/api/v1/maps/demo/1/0/0.png").status_code == 503


def test_revoked_session_denied(client, db_path):
    _login(client, db_path)
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE users SET is_locked=1 WHERE login_id='tester'")
    assert client.get("/api/v1/maps/demo/1/0/0.png").status_code == 403


def test_invalid_layer_configuration(db_path, tile_path):
    with pytest.raises(ValueError, match="layer"):
        create_app(
            database_path=db_path,
            secret_key="s" * 48,
            map_sources={"../bad": tile_path},
        )


def test_no_maps_configured(db_path):
    app = create_app(database_path=db_path, secret_key="s" * 48)
    app.config["TESTING"] = True
    create_local_user(
        db_path, login_id="tester", password="test-password",
        display_name="架空ユーザー", role_code="VIEWER",
    )
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as sess:
        csrf = sess["login_csrf"]
    client.post(
        "/login",
        data={"login_id": "tester", "password": "test-password", "csrf_token": csrf},
    )
    assert client.get("/api/v1/maps/demo/1/0/0.png").status_code == 404
