"""Server-rendered, local-only case/repair navigation and editing workflow.

Requires migration 0016. All authorization and database handles are injected.
"""

import secrets
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from road_river_management.services.case_management import get_case
from road_river_management.services.case_repair_links import (
    create_repair_for_case,
    link_repair_to_case,
    list_case_repairs,
    list_repair_cases,
    unlink_repair_from_case,
)


def _positive(raw: str | None, label: str) -> int:
    if raw is None or not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return int(raw)


def _form(allowed: set[str]) -> None:
    if set(request.form) != allowed or any(len(request.form.getlist(k)) != 1 for k in allowed):
        raise ValueError("Invalid form fields")


def create_case_repair_ui_blueprint(
    *,
    read_connection: Callable[[], AbstractContextManager[sqlite3.Connection]],
    write_connection: Callable[[], AbstractContextManager[sqlite3.Connection]],
    current_user: Callable[[], dict | None],
) -> Blueprint:
    """Create UI with role checks on each request and CSRF on every write."""
    bp = Blueprint(
        "case_repair_ui", __name__, url_prefix="/case-repairs", template_folder="templates"
    )

    @bp.before_request
    def authorize():
        try:
            user = current_user()
        except Exception:
            current_app.logger.exception("Case/repair authorization unavailable")
            abort(503)
        if not user or not (set(user.get("roles", ())) & {"ADMINISTRATOR", "EDITOR", "VIEWER"}):
            abort(403)
        if request.method == "POST":
            if not set(user.get("roles", ())) & {"ADMINISTRATOR", "EDITOR"}:
                abort(403)
            supplied = request.form.get("csrf_token", "")
            saved = session.get("case_repair_csrf", "")
            if not supplied or not saved or not secrets.compare_digest(supplied, saved):
                abort(400)

    def edit_allowed() -> bool:
        return bool(set(current_user()["roles"]) & {"ADMINISTRATOR", "EDITOR"})

    def page_data() -> dict:
        if "case_repair_csrf" not in session:
            session["case_repair_csrf"] = secrets.token_urlsafe(32)
        return {
            "csrf_token": session["case_repair_csrf"],
            "can_edit": edit_allowed(),
        }

    @bp.get("/cases/<int:case_id>")
    def case_page(case_id: int):
        with read_connection() as con:
            try:
                case = get_case(con, case_id)
                repairs = list_case_repairs(con, case_id)
            except ValueError:
                abort(404)
            facilities = con.execute(
                """SELECT f.facility_id, f.facility_name FROM managed_facilities AS f
                   JOIN administrative_case_facilities AS cf
                     ON cf.facility_id=f.facility_id WHERE cf.case_id=?
                   ORDER BY f.facility_name, f.facility_id""",
                (case_id,),
            ).fetchall()
            # Show only repairs belonging to facilities already linked to this case.
            # The database service still supports additional cross-facility links.
            candidates = con.execute(
                """SELECT r.repair_id, r.repair_name, f.facility_name
                   FROM facility_repairs AS r
                   JOIN managed_facilities AS f ON f.facility_id=r.facility_id
                   JOIN administrative_case_facilities AS cf ON cf.facility_id=f.facility_id
                   WHERE cf.case_id=? AND NOT EXISTS (
                       SELECT 1 FROM case_repair_links AS l WHERE l.case_id=?
                         AND l.repair_id=r.repair_id AND l.unlinked_at IS NULL
                   ) ORDER BY r.repair_id DESC LIMIT 100""",
                (case_id, case_id),
            ).fetchall()
        return render_template(
            "case_repair_case.html", case=case, repairs=repairs,
            facilities=facilities, candidates=candidates, **page_data()
        )

    @bp.get("/repairs/<int:repair_id>")
    def repair_page(repair_id: int):
        with read_connection() as con:
            cursor = con.execute(
                """SELECT r.repair_id, r.repair_name, r.repair_type,
                          r.repair_description, r.started_on, r.completed_on,
                          r.actual_cost_yen, f.facility_id, f.facility_name
                   FROM facility_repairs AS r JOIN managed_facilities AS f
                     ON f.facility_id=r.facility_id WHERE r.repair_id=?""",
                (repair_id,),
            )
            item = cursor.fetchone()
            if item is None:
                abort(404)
            repair = dict(zip((c[0] for c in cursor.description), item, strict=True))
            related = list_repair_cases(con, repair_id)
        return render_template("case_repair_repair.html", repair=repair, cases=related,
                               **page_data())

    def perform(action: Callable[[sqlite3.Connection, int], object], case_id: int):
        user = current_user()
        try:
            with write_connection() as con:
                action(con, user["id"])
                con.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            current_app.logger.info("Rejected case-repair operation: %s", exc)
            abort(400, description="入力または関連付け条件を確認してください")
        return redirect(url_for("case_repair_ui.case_page", case_id=case_id), code=303)

    @bp.post("/cases/<int:case_id>/link")
    def link(case_id: int):
        try:
            _form({"csrf_token", "repair_id"})
            rid = _positive(request.form["repair_id"], "repair_id")
        except ValueError:
            abort(400)
        return perform(lambda con, uid: link_repair_to_case(
            con, case_id=case_id, repair_id=rid, actor_user_id=uid
        ), case_id)

    @bp.post("/cases/<int:case_id>/create")
    def create(case_id: int):
        try:
            _form({"csrf_token", "facility_id", "repair_name", "repair_type",
                   "repair_description"})
            fid = _positive(request.form["facility_id"], "facility_id")
            name = request.form["repair_name"].strip()
            kind = request.form["repair_type"]
            desc = request.form["repair_description"].strip() or None
            if not name or len(name) > 200 or (desc and len(desc) > 2000):
                raise ValueError("Invalid repair text length")
        except ValueError:
            abort(400)

        def action(con: sqlite3.Connection, uid: int):
            # Never accept an arbitrary facility ID from a forged form.
            if con.execute(
                """SELECT 1 FROM administrative_case_facilities
                   WHERE case_id=? AND facility_id=?""", (case_id, fid)
            ).fetchone() is None:
                raise ValueError("Facility not linked to case")
            create_repair_for_case(
                con, case_id=case_id, facility_id=fid, repair_name=name,
                repair_type=kind, repair_description=desc, actor_user_id=uid,
            )
        return perform(action, case_id)

    @bp.post("/cases/<int:case_id>/unlink")
    def unlink(case_id: int):
        try:
            _form({"csrf_token", "repair_id", "reason"})
            rid = _positive(request.form["repair_id"], "repair_id")
            reason = request.form["reason"].strip()
            if not reason or len(reason) > 500:
                raise ValueError("Unlink reason required")
        except ValueError:
            abort(400)
        return perform(lambda con, uid: unlink_repair_from_case(
            con, case_id=case_id, repair_id=rid, actor_user_id=uid, reason=reason
        ), case_id)

    return bp
