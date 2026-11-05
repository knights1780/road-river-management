"""Photo UI regression tests: permissions, images, hidden history, and rollback."""

import io
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

import pytest
from flask import Flask
from PIL import Image

from road_river_management.database import migrate
from road_river_management.web.photo_ui import create_photo_blueprint


def _image_bytes():
    raw = io.BytesIO()
    Image.new("RGB", (80, 60), "green").save(raw, format="PNG")
    return raw.getvalue()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite"
    con = migrate.get_connection(path)
    migrate.ensure_schema_versions(con)
    migrate.upgrade(con)
    uid = con.execute(
        """INSERT INTO users (user_uuid, login_id, display_name,
           password_hash, password_algorithm)
           VALUES (?, 'photo-ui', '写真職員', 'unused', 'TEST')""",
        (str(uuid4()),),
    ).lastrowid
    typ = con.execute(
        "SELECT facility_type_id FROM facility_types WHERE type_code='BRIDGE'"
    ).fetchone()[0]
    status = con.execute(
        "SELECT facility_status_id FROM facility_statuses WHERE status_code='ACTIVE'"
    ).fetchone()[0]
    fid = con.execute(
        """INSERT INTO managed_facilities
           (facility_uuid, facility_name, facility_type_id, facility_status_id)
           VALUES (?, '試験橋梁', ?, ?)""", (str(uuid4()), typ, status),
    ).lastrowid
    rid = con.execute(
        """INSERT INTO facility_repairs (repair_uuid, facility_id, repair_name, repair_type)
           VALUES (?, ?, '高欄補修', 'PLANNED')""", (str(uuid4()), fid),
    ).lastrowid
    con.commit()
    con.close()
    return path, uid, fid, rid, tmp_path / "photos"


def _client(database, role="EDITOR"):
    path, uid, *_rest, root = database
    app = Flask(__name__)
    app.secret_key = "tests-only"
    # Production app normally has a restrictive 16KiB default.
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    user = {"id": uid, "roles": {role}} if role is not None else None

    @contextmanager
    def connect():
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
        finally:
            con.close()

    app.register_blueprint(create_photo_blueprint(
        read_connection=connect, write_connection=connect,
        current_user=lambda: user, storage_root=root,
    ))
    return app.test_client()


def _upload(client, kind, parent_id, *, csrf=None, image=None, keep_original="0"):
    return client.post(f"/photos/{kind}/{parent_id}/upload", data={
        "csrf_token": csrf or "",
        "category": "BEFORE",
        "caption": "修繕前の状況",
        "photographed_on": "2026-11-05",
        "keep_original": keep_original,
        "photo": (io.BytesIO(image or _image_bytes()), "photo.png"),
    }, content_type="multipart/form-data")


def _csrf(client, kind, parent_id):
    response = client.get(f"/photos/{kind}s/{parent_id}")
    assert response.status_code == 200
    with client.session_transaction() as sess:
        return sess["photo_csrf"]


def test_upload_view_hide_and_audit(database):
    path, _uid, fid, _rid, root = database
    client = _client(database)
    response = client.get(f"/photos/facilities/{fid}")
    assert response.status_code == 200
    with client.session_transaction() as sess:
        token = sess["photo_csrf"]
    result = _upload(client, "facility", fid, csrf=token, keep_original="1")
    assert result.status_code == 303
    with sqlite3.connect(path) as con:
        pid, webp_name, original_name = con.execute(
            "SELECT photo_id, stored_filename, original_stored_filename FROM facility_photos"
        ).fetchone()
        assert con.execute("SELECT event_type FROM facility_photo_events").fetchone()[0] == "REGISTERED"
    assert (root / webp_name).exists()
    assert (root / original_name).exists()
    image = client.get(f"/photos/image/{pid}")
    assert image.status_code == 200
    assert image.mimetype == "image/webp"
    assert Image.open(io.BytesIO(image.data)).format == "WEBP"
    hidden = client.post(f"/photos/image/{pid}/hide", data={
        "csrf_token": token, "reason": "写真の重複登録",
    })
    assert hidden.status_code == 303
    assert client.get(f"/photos/image/{pid}").status_code == 404
    with sqlite3.connect(path) as con:
        assert [r[0] for r in con.execute(
            "SELECT event_type FROM facility_photo_events ORDER BY event_id"
        )] == ["REGISTERED", "HIDDEN"]
        assert con.execute("SELECT hidden_reason FROM facility_photos").fetchone()[0] == "写真の重複登録"
    assert (root / webp_name).exists()  # Audit preservation; no physical deletion.


def test_repair_gallery_and_upload(database):
    path, _uid, _fid, rid, _root = database
    client = _client(database)
    token = _csrf(client, "repair", rid)
    assert _upload(client, "repair", rid, csrf=token).status_code == 303
    assert "修繕前の状況" in client.get(f"/photos/repairs/{rid}").get_data(as_text=True)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT facility_id, repair_id FROM facility_photos").fetchone() == (None, rid)


def test_viewer_can_view_but_not_upload(database):
    _path, _uid, fid, _rid, _root = database
    client = _client(database, "VIEWER")
    assert client.get(f"/photos/facilities/{fid}").status_code == 200
    with client.session_transaction() as sess:
        token = sess["photo_csrf"]
    assert _upload(client, "facility", fid, csrf=token).status_code == 403


def test_unauthenticated_forbidden(database):
    _path, _uid, fid, _rid, _root = database
    assert _client(database, None).get(f"/photos/facilities/{fid}").status_code == 403


def test_csrf_required(database):
    _path, _uid, fid, _rid, _root = database
    assert _upload(_client(database), "facility", fid).status_code == 400


def test_broken_image_does_not_create_metadata(database):
    path, _uid, fid, _rid, root = database
    client = _client(database)
    client.get(f"/photos/facilities/{fid}")
    with client.session_transaction() as sess:
        token = sess["photo_csrf"]
    assert _upload(client, "facility", fid, csrf=token, image=b"broken-image").status_code == 400
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM facility_photos").fetchone()[0] == 0
    if root.exists():
        assert list(root.rglob("*.webp")) == []


def test_missing_parent_does_not_write(database):
    path, _uid, fid, _rid, root = database
    client = _client(database)
    client.get(f"/photos/facilities/{fid}")
    with client.session_transaction() as sess:
        token = sess["photo_csrf"]
    assert _upload(client, "facility", 99999, csrf=token).status_code == 404
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM facility_photos").fetchone()[0] == 0
    assert not root.exists()


def test_unsafe_metadata_path_never_served(database):
    path, uid, fid, _rid, _root = database
    client = _client(database)
    with sqlite3.connect(path) as con:
        con.execute("""INSERT INTO facility_photos
            (photo_uuid, facility_id, photo_category, stored_filename,
             stored_bytes, stored_sha256, created_by_user_id)
            VALUES (?, ?, 'OVERVIEW', '../../secret.webp', 1, ?, ?)""",
            (str(uuid4()), fid, "0" * 64, uid),
        )
        pid = con.execute("SELECT photo_id FROM facility_photos").fetchone()[0]
    assert client.get(f"/photos/image/{pid}").status_code == 404


def test_reason_required_for_hide(database):
    path, _uid, fid, _rid, _root = database
    client = _client(database)
    client.get(f"/photos/facilities/{fid}")
    with client.session_transaction() as sess:
        token = sess["photo_csrf"]
    assert _upload(client, "facility", fid, csrf=token).status_code == 303
    with sqlite3.connect(path) as con:
        pid = con.execute("SELECT photo_id FROM facility_photos").fetchone()[0]
    assert client.post(f"/photos/image/{pid}/hide", data={
        "csrf_token": token, "reason": "",
    }).status_code == 400
