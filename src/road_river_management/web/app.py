"""Offline-first Flask UI with database-backed local authentication.

Do not expose the development server to the LAN. The local browser interface
binds to 127.0.0.1; production packaging should use a reviewed WSGI host.
"""

import argparse
import getpass
import os
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from road_river_management.services.budget_dashboard import get_integrated_fiscal_dashboard
from road_river_management.services.carryover_reports import get_carryover_list_report
from road_river_management.web.dashboard_api import create_dashboard_blueprint
from road_river_management.web.exports_api import create_exports_blueprint

ROLE_CODES = frozenset({"ADMINISTRATOR", "EDITOR", "VIEWER"})
FAILED_LIMIT = 5


def _connect(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(db_path, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def _read_connection(db_path: Path):
    connection = _connect(db_path, read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def _parse_year(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal():
        raise ValueError("fiscal_year must be an integer")
    year = int(value)
    if not 1900 <= year <= 2200:
        raise ValueError("fiscal_year out of range")
    return year


def _parse_project(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError("project_id must be positive")
    return int(value)


def create_app(*, database_path: str | Path, secret_key: str) -> Flask:
    """Create an app; both database path and secret must be supplied explicitly."""
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise ValueError("A random secret_key of at least 32 characters is required")
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = secret_key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    def current_user():
        if "authenticated_user" in g:
            return g.authenticated_user
        uid = session.get("user_id")
        if type(uid) is not int or uid <= 0:
            if "user_id" in session:
                session.clear()
            g.authenticated_user = None
            return None
        with _read_connection(path) as con:
            row = con.execute(
                """SELECT user_id, login_id, display_name FROM users
                   WHERE user_id=? AND is_active=1 AND is_locked=0
                     AND auth_provider='LOCAL'""",
                (uid,),
            ).fetchone()
            if row:
                roles = {
                    r[0]
                    for r in con.execute(
                        """SELECT roles.role_code FROM user_roles AS ur
                           JOIN roles ON roles.role_id=ur.role_id
                           WHERE ur.user_id=? AND ur.revoked_at IS NULL
                             AND roles.is_active=1""",
                        (uid,),
                    )
                }
            else:
                roles = set()
        # A suspended account or a user with no active permitted role must not
        # retain a valid login cookie after an administrator changes access.
        if row is None or not (roles & ROLE_CODES):
            session.clear()
            g.authenticated_user = None
            return None
        g.authenticated_user = {
            "id": row[0], "login": row[1], "display": row[2], "roles": roles
        }
        return g.authenticated_user

    def can_view():
        user = current_user()
        return user is not None and bool(user["roles"] & ROLE_CODES)

    app.register_blueprint(
        create_dashboard_blueprint(
            connection_factory=lambda: _read_connection(path), can_view=can_view
        )
    )

    app.register_blueprint(
        create_exports_blueprint(
            connection_factory=lambda: _read_connection(path), can_view=can_view
        )
    )

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index():
        return redirect(url_for("dashboard") if can_view() else url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if can_view():
                return redirect(url_for("dashboard"))
            token = secrets.token_urlsafe(32)
            session["login_csrf"] = token
            return render_template("login.html", csrf_token=token, error=None)

        login_id = request.form.get("login_id", "").strip()
        password = request.form.get("password", "")
        token = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token, session.get("login_csrf", "")):
            abort(400)
        if not login_id or not password or len(login_id) > 255 or len(password) > 4096:
            return render_template(
                "login.html", csrf_token=session["login_csrf"], error="入力内容を確認してください"
            ), 400

        with closing(_connect(path, read_only=False)) as con, con:
            user = con.execute(
                """SELECT user_id, password_hash, password_algorithm, is_active, is_locked
                   FROM users WHERE login_id=? AND auth_provider='LOCAL'""",
                (login_id,),
            ).fetchone()
            cutoff = (datetime.now(UTC) - timedelta(minutes=15)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            recent_failures = con.execute(
                """SELECT COUNT(*) FROM user_login_history
                   WHERE attempted_login_id=? COLLATE NOCASE
                     AND result_code='INVALID_CREDENTIALS'
                     AND login_at >= ?""",
                (login_id, cutoff),
            ).fetchone()[0]
            locked_out = recent_failures >= FAILED_LIMIT
            success = (
                not locked_out
                and user is not None
                and user[3] == 1
                and user[4] == 0
                and user[2] == "WERKZEUG"
                and check_password_hash(user[1], password)
            )
            result_code = "SUCCESS" if success else "INVALID_CREDENTIALS"
            if user and user[3] == 0:
                result_code = "ACCOUNT_DISABLED"
            elif user and user[4] == 1:
                result_code = "ACCOUNT_LOCKED"
            con.execute(
                """INSERT INTO user_login_history (
                       user_id, attempted_login_id, result_code
                   ) VALUES (?, ?, ?)""",
                (user[0] if user else None, login_id, result_code),
            )
            if success:
                con.execute(
                    "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE user_id=?",
                    (user[0],),
                )

        if not success:
            return render_template(
                "login.html", csrf_token=session["login_csrf"],
                error="ログインできません。ID・パスワード・利用状態を確認してください",
            ), 401
        session.clear()
        session.permanent = True
        session["user_id"] = user[0]
        session["logout_csrf"] = secrets.token_urlsafe(32)
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout():
        token = request.form.get("csrf_token", "")
        if not token or not secrets.compare_digest(token, session.get("logout_csrf", "")):
            abort(400)
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    def dashboard():
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if not can_view():
            abort(403)
        if set(request.args) - {"fiscal_year", "project_id"}:
            abort(400)
        if any(len(request.args.getlist(k)) != 1 for k in request.args):
            abort(400)
        try:
            year = _parse_year(request.args.get("fiscal_year", str(datetime.now().year)))
            project_id = _parse_project(request.args.get("project_id"))
        except ValueError:
            abort(400)
        with _read_connection(path) as con:
            try:
                report = get_integrated_fiscal_dashboard(con, year, project_id=project_id)
                carryovers = get_carryover_list_report(con, year, project_id=project_id)
            except ValueError:
                abort(404)
            projects = con.execute(
                "SELECT project_id, project_name FROM maintenance_projects ORDER BY project_id"
            ).fetchall()
        return render_template(
            "dashboard.html", user=user, fiscal_year=year, project_id=project_id,
            report=report, carryovers=carryovers, projects=projects,
            logout_csrf=session.get("logout_csrf", ""),
        )

    return app


def create_local_user(database_path: str | Path, *, login_id: str, password: str,
                      display_name: str, role_code: str = "ADMINISTRATOR") -> int:
    """Bootstrap a local account through an explicit CLI operation."""
    if role_code not in ROLE_CODES or not login_id.strip() or not display_name.strip():
        raise ValueError("Invalid account information")
    if not password:
        raise ValueError("Password must not be empty")
    with closing(_connect(Path(database_path), read_only=False)) as con, con:
        role = con.execute(
            "SELECT role_id FROM roles WHERE role_code=? AND is_active=1", (role_code,)
        ).fetchone()
        if role is None:
            raise ValueError("Role not found: apply migrations before creating users")
        cursor = con.execute(
            """INSERT INTO users (
                user_uuid, login_id, display_name,
                password_hash, password_algorithm
            ) VALUES (?, ?, ?, ?, 'WERKZEUG')""",
            (str(uuid4()), login_id.strip(), display_name.strip(),
             generate_password_hash(password, method="scrypt")),
        )
        uid = cursor.lastrowid
        con.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (uid, role[0]))
    return uid


def main():
    parser = argparse.ArgumentParser(description="Offline civil infrastructure web application")
    parser.add_argument("command", choices=["run", "create-user"])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--login")
    parser.add_argument("--display")
    parser.add_argument("--role", choices=sorted(ROLE_CODES), default="ADMINISTRATOR")
    args = parser.parse_args()
    if args.command == "create-user":
        if not args.login or not args.display:
            parser.error("--login and --display are required")
        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords differ")
        uid = create_local_user(
            args.db, login_id=args.login, password=password,
            display_name=args.display, role_code=args.role,
        )
        print(f"Created local user ID: {uid}")
    else:
        secret = os.environ.get("RRM_SECRET_KEY")
        if not secret:
            parser.error("RRM_SECRET_KEY environment variable must be set")
        app = create_app(database_path=args.db, secret_key=secret)
        app.run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
