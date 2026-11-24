import sqlite3

from flask import Blueprint, current_app, g, jsonify, request

from app.db import get_db, utc_now_iso, write_transaction
from app.routes.auth import require_super_admin
from app.services.audit import log_audit
from app.services.users import (
    VALID_ROLES,
    hash_password,
    normalize_username,
    serialize_user,
    validate_password_strength,
)


users_bp = Blueprint("users", __name__)


@users_bp.route("/api/users", methods=["GET"])
@require_super_admin
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM users ORDER BY CASE role WHEN 'super_admin' THEN 0 ELSE 1 END, username ASC"
    ).fetchall()
    return jsonify({"users": [serialize_user(r) for r in rows]})


@users_bp.route("/api/users", methods=["POST"])
@require_super_admin
def create_user():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    username = normalize_username(body.get("username"))
    if not username:
        return (
            jsonify(
                {
                    "error": "Username must be 3-40 characters (lowercase letters, numbers, '.', '_', '-')"
                }
            ),
            400,
        )

    display_name = str(body.get("displayName") or "").strip()[:80]
    if not display_name:
        return jsonify({"error": "Display name is required"}), 400

    role = str(body.get("role") or "admin").strip().lower()
    if role not in VALID_ROLES:
        return jsonify({"error": "Role must be 'admin' or 'super_admin'"}), 400

    password = str(body.get("password") or "")
    min_len = int(current_app.config.get("MIN_PASSWORD_LENGTH", 10))
    pwd_err = validate_password_strength(password, min_length=min_len)
    if pwd_err:
        return jsonify({"error": pwd_err}), 400

    must_change = 1 if body.get("mustChangePassword", True) else 0
    db = get_db()
    now = utc_now_iso()

    try:
        with write_transaction(db):
            cur = db.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, role, is_active, must_change_password, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (username, display_name, hash_password(password), role, must_change, now),
            )
            new_id = cur.lastrowid
            log_audit(
                db,
                action="user_created",
                target_type="user",
                target_id=str(new_id),
                details={"username": username, "displayName": display_name, "role": role},
            )
            row = db.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Username '{username}' already exists"}), 409

    return jsonify({"ok": True, "user": serialize_user(row)}), 201


@users_bp.route("/api/users/<int:user_id>", methods=["PATCH"])
@require_super_admin
def update_user(user_id: int):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"User #{user_id} not found"}), 404

        display_name = existing["display_name"]
        if "displayName" in body:
            display_name = str(body["displayName"] or "").strip()[:80]
            if not display_name:
                return jsonify({"error": "Display name cannot be empty"}), 400

        role = existing["role"]
        if "role" in body:
            role = str(body["role"] or "").strip().lower()
            if role not in VALID_ROLES:
                return jsonify({"error": "Role must be 'admin' or 'super_admin'"}), 400

        is_active = existing["is_active"]
        if "isActive" in body:
            is_active = 1 if body["isActive"] else 0

        # Protect against locking out the system by removing the last active super_admin
        was_active_super = existing["role"] == "super_admin" and existing["is_active"] == 1
        will_be_active_super = role == "super_admin" and is_active == 1
        if was_active_super and not will_be_active_super:
            other_supers = db.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'super_admin' AND is_active = 1 AND id != ?",
                (user_id,),
            ).fetchone()[0]
            if other_supers == 0:
                return (
                    jsonify({"error": "Cannot deactivate or demote the last active super admin"}),
                    400,
                )

        if user_id == g.current_user["id"] and not is_active:
            return jsonify({"error": "You cannot deactivate your own active account"}), 400

        must_change = existing["must_change_password"]
        if "mustChangePassword" in body:
            must_change = 1 if body["mustChangePassword"] else 0

        password_hash = existing["password_hash"]
        password_reset = False
        if "password" in body and body["password"]:
            min_len = int(current_app.config.get("MIN_PASSWORD_LENGTH", 10))
            pwd_err = validate_password_strength(str(body["password"]), min_length=min_len)
            if pwd_err:
                return jsonify({"error": pwd_err}), 400
            password_hash = hash_password(str(body["password"]))
            password_reset = True
            if "mustChangePassword" not in body:
                must_change = 1

        db.execute(
            """
            UPDATE users
            SET display_name = ?, role = ?, is_active = ?, must_change_password = ?, password_hash = ?
            WHERE id = ?
            """,
            (display_name, role, is_active, must_change, password_hash, user_id),
        )
        log_audit(
            db,
            action="user_updated",
            target_type="user",
            target_id=str(user_id),
            details={
                "username": existing["username"],
                "role": role,
                "isActive": bool(is_active),
                "passwordReset": password_reset,
            },
        )
        updated = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return jsonify({"ok": True, "user": serialize_user(updated)})
