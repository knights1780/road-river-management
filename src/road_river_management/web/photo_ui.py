"""Authenticated facility/repair photo workflow (migration 0017 required).

Stores only opaque relative filenames in SQLite; never serves originals.
"""

import secrets
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path, PurePosixPath

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from road_river_management.services.photo_storage import (
    DEFAULT_MAX_UPLOAD_BYTES,
    PhotoValidationError,
    save_photo,
)

CATEGORIES = ("OVERVIEW", "DAMAGE", "BEFORE", "DURING", "AFTER", "INSPECTION", "OTHER")
MAX_FORM_BYTES = DEFAULT_MAX_UPLOAD_BYTES + 1024 * 1024


def _user_with_role(current_user: Callable[[], dict | None]) -> dict:
    try:
        user = current_user()
    except Exception:
        current_app.logger.exception("Photo authorization backend unavailable")
        abort(503)
    if not user or not (set(user.get("roles", ())) & {"ADMINISTRATOR", "EDITOR", "VIEWER"}):
        abort(403)
    return user


def _file_path(root: Path, stored_filename: str) -> Path:
    """Reject paths under attacker-controlled DB metadata or symlinks."""
    if not isinstance(stored_filename, str) or "\\" in stored_filename:
        raise ValueError("Invalid stored path")
    relative = PurePosixPath(stored_filename)
    if (relative.is_absolute() or len(relative.parts) != 3
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix != ".webp"):
        raise ValueError("Invalid stored path")
    target = root.joinpath(*relative.parts)
    if target.resolve() != target.absolute() or not target.resolve().is_relative_to(root):
        raise ValueError("Invalid stored path")
    return target


def _photo_id(raw: int) -> int:
    if type(raw) is not int or raw < 1:
        abort(404)
    return raw


def _photo_date(raw: str) -> str | None:
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise PhotoValidationError("Invalid photographed_on") from exc
    if parsed.isoformat() != raw or not 1900 <= parsed.year <= 2200:
        raise PhotoValidationError("Invalid photographed_on")
    return raw


def create_photo_blueprint(
    *,
    read_connection: Callable[[], AbstractContextManager[sqlite3.Connection]],
    write_connection: Callable[[], AbstractContextManager[sqlite3.Connection]],
    current_user: Callable[[], dict | None],
    storage_root: str | Path,
) -> Blueprint:
    """Register GET/POST routes with a stable explicit photo storage directory."""
    root = Path(storage_root).expanduser().resolve()
    bp = Blueprint("photo_ui", __name__, url_prefix="/photos", template_folder="templates")

    @bp.before_request
    def authorize():
        # Apply the route-specific upload limit before parsing multipart form data.
        if request.endpoint == "photo_ui.upload":
            request.max_content_length = MAX_FORM_BYTES
        user = _user_with_role(current_user)
        if request.method == "POST":
            if not (set(user["roles"]) & {"ADMINISTRATOR", "EDITOR"}):
                abort(403)
            supplied = request.form.get("csrf_token", "")
            expected = session.get("photo_csrf", "")
            if not supplied or not expected or not secrets.compare_digest(supplied, expected):
                abort(400)

    def _page_data(kind: str, parent_id: int):
        table, key = (("managed_facilities", "facility_id") if kind == "facility"
                      else ("facility_repairs", "repair_id"))
        with read_connection() as con:
            parent = con.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (parent_id,)).fetchone()
            if parent is None:
                abort(404)
            cursor = con.execute(
                f"""SELECT photo_id, photo_category, photographed_on, caption, created_at
                    FROM facility_photos WHERE {key}=? AND hidden_at IS NULL
                    ORDER BY photographed_on DESC, photo_id DESC""", (parent_id,)
            )
            cols = [entry[0] for entry in cursor.description]
            photos = [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
        if "photo_csrf" not in session:
            session["photo_csrf"] = secrets.token_urlsafe(32)
        user = _user_with_role(current_user)
        return render_template(
            "photo_gallery.html", kind=kind, parent_id=parent_id, photos=photos,
            csrf_token=session["photo_csrf"], categories=CATEGORIES,
            can_edit=bool(set(user["roles"]) & {"ADMINISTRATOR", "EDITOR"}),
        )

    @bp.get("/facilities/<int:parent_id>")
    def facility_gallery(parent_id: int):
        return _page_data("facility", _photo_id(parent_id))

    @bp.get("/repairs/<int:parent_id>")
    def repair_gallery(parent_id: int):
        return _page_data("repair", _photo_id(parent_id))

    @bp.post("/<kind>/<int:parent_id>/upload")
    def upload(kind: str, parent_id: int):
        if kind not in {"facility", "repair"}:
            abort(404)
        _photo_id(parent_id)
        if (set(request.form) != {"csrf_token", "category", "caption", "photographed_on", "keep_original"}
                or any(len(request.form.getlist(key)) != 1 for key in request.form)
                or set(request.files) != {"photo"} or len(request.files.getlist("photo")) != 1):
            abort(400)
        category = request.form["category"]
        caption = request.form["caption"].strip()
        if category not in CATEGORIES or len(caption) > 1000:
            abort(400)
        try:
            photographed_on = _photo_date(request.form["photographed_on"].strip())
        except PhotoValidationError:
            abort(400)
        preserve = request.form["keep_original"]
        if preserve not in {"0", "1"}:
            abort(400)
        file = request.files["photo"]
        if not file.filename:
            abort(400)
        table, key = (("managed_facilities", "facility_id") if kind == "facility"
                      else ("facility_repairs", "repair_id"))
        with read_connection() as con:
            if con.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (parent_id,)).fetchone() is None:
                abort(404)
        try:
            stored = save_photo(
                file.stream, original_filename=file.filename, storage_root=root,
                keep_original=preserve == "1",
            )
        except (PhotoValidationError, ValueError):
            abort(400, description="画像ファイルまたは入力内容を確認してください")
        user = _user_with_role(current_user)
        try:
            with write_connection() as con:
                con.execute("BEGIN IMMEDIATE")
                cursor = con.execute(
                    """INSERT INTO facility_photos (
                       photo_uuid, facility_id, repair_id, photo_category, photographed_on,
                       caption, stored_filename, stored_media_type, stored_bytes, stored_sha256,
                       original_filename, original_stored_filename, original_media_type,
                       original_bytes, original_sha256, created_by_user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (secrets.token_hex(16), parent_id if kind == "facility" else None,
                     parent_id if kind == "repair" else None, category,
                     photographed_on or stored.photographed_on, caption,
                     stored.stored_filename, stored.stored_media_type,
                     stored.stored_bytes, stored.stored_sha256, stored.original_filename,
                     stored.original_stored_filename, stored.original_media_type,
                     stored.original_bytes, stored.original_sha256, user["id"]),
                )
                con.execute(
                    """INSERT INTO facility_photo_events (photo_id, event_type, actor_user_id)
                       VALUES (?, 'REGISTERED', ?)""", (cursor.lastrowid, user["id"]),
                )
                con.commit()
        except Exception:
            # Best-effort cleanup of files created before a failed DB transaction.
            for relative in (stored.stored_filename, stored.original_stored_filename):
                if relative:
                    try:
                        (root / relative).unlink(missing_ok=True)
                    except OSError:
                        current_app.logger.exception("Failed to clean up photo after DB error")
            raise
        endpoint = "photo_ui.facility_gallery" if kind == "facility" else "photo_ui.repair_gallery"
        return redirect(url_for(endpoint, parent_id=parent_id), code=303)

    @bp.get("/image/<int:photo_id>")
    def image(photo_id: int):
        with read_connection() as con:
            row = con.execute(
                """SELECT stored_filename FROM facility_photos
                   WHERE photo_id=? AND hidden_at IS NULL""", (_photo_id(photo_id),)
            ).fetchone()
        if row is None:
            abort(404)
        try:
            target = _file_path(root, row[0])
        except ValueError:
            current_app.logger.error("Unsafe stored photo path for photo_id=%s", photo_id)
            abort(404)
        if not target.is_file():
            abort(404)
        return send_file(target, mimetype="image/webp", conditional=False)

    @bp.post("/image/<int:photo_id>/hide")
    def hide(photo_id: int):
        if set(request.form) != {"csrf_token", "reason"} or any(
            len(request.form.getlist(key)) != 1 for key in request.form
        ):
            abort(400)
        reason = request.form["reason"].strip()
        if not 1 <= len(reason) <= 500:
            abort(400)
        user = _user_with_role(current_user)
        with write_connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT facility_id, repair_id, hidden_at FROM facility_photos WHERE photo_id=?",
                (_photo_id(photo_id),),
            ).fetchone()
            if row is None or row[2] is not None:
                abort(404)
            con.execute(
                """UPDATE facility_photos
                   SET hidden_at=CURRENT_TIMESTAMP, hidden_by_user_id=?, hidden_reason=?
                   WHERE photo_id=? AND hidden_at IS NULL""",
                (user["id"], reason, photo_id),
            )
            con.execute(
                """INSERT INTO facility_photo_events
                   (photo_id, event_type, actor_user_id, reason)
                   VALUES (?, 'HIDDEN', ?, ?)""", (photo_id, user["id"], reason),
            )
            con.commit()
        endpoint = "photo_ui.facility_gallery" if row[0] is not None else "photo_ui.repair_gallery"
        return redirect(url_for(endpoint, parent_id=row[0] or row[1]), code=303)

    return bp
