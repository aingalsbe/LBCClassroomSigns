from functools import wraps

from flask import Blueprint, current_app, g, jsonify, make_response, request, session
from werkzeug.security import check_password_hash

from app.db import get_db, utc_now_iso, write_transaction
from app.security import (
    check_login_rate_limit,
    clear_user_rate_limit,
    ensure_csrf_token,
    get_client_ip,
    record_login_failure,
    rotate_csrf_token,
    verify_csrf_token,
)
from app.services.audit import log_audit
from app.services.users import (
    authenticate_user,
    hash_password,
    serialize_user,
    validate_password_strength,
)


auth_bp = Blueprint("auth", __name__)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def load_session_user():
    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return None
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["is_active"]:
        session.clear()
        g.current_user = None
        return None
    g.current_user = row
    return row


def require_admin(view_fn):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        user = load_session_user()
        if not user:
            return jsonify({"error": "Admin login required"}), 401
        if request.method in MUTATING_METHODS and not verify_csrf_token():
            return jsonify({"error": "Invalid or missing CSRF token", "code": "CSRF_ERROR"}), 403
        return view_fn(*args, **kwargs)

    return wrapped


def require_super_admin(view_fn):
    @wraps(view_fn)
    @require_admin
    def wrapped(*args, **kwargs):
        if g.current_user["role"] != "super_admin":
            return jsonify({"error": "Super admin privileges required"}), 403
        return view_fn(*args, **kwargs)

    return wrapped


@auth_bp.route("/api/auth", methods=["GET"])
def auth_status():
    user = load_session_user()
    token = ensure_csrf_token()
    return jsonify(
        {
            "authenticated": bool(user),
            "csrfToken": token,
            "user": serialize_user(user) if user else None,
        }
    )


@auth_bp.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    if not username or not password:
        return jsonify({"ok": False, "error": "Username and password are required"}), 400

    db = get_db()
    ip_addr = get_client_ip()

    allowed, retry_after = check_login_rate_limit(db, ip_addr, username)
    if not allowed:
        with write_transaction(db):
            log_audit(
                db,
                action="login_rate_limited",
                target_type="user",
                target_id=username,
                details={"retryAfterSeconds": retry_after},
                username=username,
                actor_ip=ip_addr,
            )
        resp = make_response(
            jsonify(
                {
                    "ok": False,
                    "error": f"Too many failed login attempts. Try again in {retry_after} seconds.",
                    "code": "RATE_LIMITED",
                    "retryAfter": retry_after,
                }
            ),
            429,
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    user = authenticate_user(db, username, password)
    if not user:
        locked_now, lock_retry = record_login_failure(db, ip_addr, username)
        with write_transaction(db):
            log_audit(
                db,
                action="login_failed",
                target_type="user",
                target_id=username,
                details={"lockedNow": locked_now},
                username=username,
                actor_ip=ip_addr,
            )
        if locked_now:
            resp = make_response(
                jsonify(
                    {
                        "ok": False,
                        "error": f"Too many failed login attempts. Account/IP locked for {lock_retry} seconds.",
                        "code": "RATE_LIMITED",
                        "retryAfter": lock_retry,
                    }
                ),
                429,
            )
            resp.headers["Retry-After"] = str(lock_retry)
            return resp
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401

    clear_user_rate_limit(db, user["username"])
    now = utc_now_iso()
    with write_transaction(db):
        db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user["id"]))
        log_audit(
            db,
            action="login_success",
            target_type="user",
            target_id=str(user["id"]),
            details={"role": user["role"]},
            user_id=user["id"],
            username=user["username"],
            actor_ip=ip_addr,
        )
        fresh_user = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

    session.clear()
    session["user_id"] = fresh_user["id"]
    session["username"] = fresh_user["username"]
    session["role"] = fresh_user["role"]
    session.permanent = True
    csrf_token = rotate_csrf_token()
    g.current_user = fresh_user

    return jsonify({"ok": True, "csrfToken": csrf_token, "user": serialize_user(fresh_user)})


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    user = load_session_user()
    if user:
        if not verify_csrf_token():
            return jsonify({"error": "Invalid or missing CSRF token", "code": "CSRF_ERROR"}), 403
        db = get_db()
        with write_transaction(db):
            log_audit(
                db,
                action="logout",
                target_type="user",
                target_id=str(user["id"]),
                user_id=user["id"],
                username=user["username"],
                actor_ip=get_client_ip(),
            )
    session.clear()
    g.current_user = None
    return jsonify({"ok": True, "csrfToken": ensure_csrf_token()})


@auth_bp.route("/api/auth/change-password", methods=["POST"])
@require_admin
def change_own_password():
    body = request.get_json(silent=True) or {}
    current_pwd = str(body.get("currentPassword") or "")
    new_pwd = str(body.get("newPassword") or "")

    user = g.current_user
    if not check_password_hash(user["password_hash"], current_pwd):
        return jsonify({"error": "Current password is incorrect"}), 400

    min_len = int(current_app.config.get("MIN_PASSWORD_LENGTH", 10))
    err = validate_password_strength(new_pwd, min_length=min_len)
    if err:
        return jsonify({"error": err}), 400
    if current_pwd == new_pwd:
        return jsonify({"error": "New password must differ from current password"}), 400

    db = get_db()
    with write_transaction(db):
        db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new_pwd), user["id"]),
        )
        log_audit(
            db,
            action="password_changed",
            target_type="user",
            target_id=str(user["id"]),
            details={"selfService": True},
        )
        updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

    csrf_token = rotate_csrf_token()
    g.current_user = updated
    return jsonify({"ok": True, "csrfToken": csrf_token, "user": serialize_user(updated)})
