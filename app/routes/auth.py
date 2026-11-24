from functools import wraps
import hmac

from flask import Blueprint, current_app, jsonify, request, session


auth_bp = Blueprint("auth", __name__)


def require_admin(view_fn):
    @wraps(view_fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"error": "Admin login required"}), 401
        return view_fn(*args, **kwargs)

    return wrapped


@auth_bp.route("/api/auth", methods=["GET"])
def auth_status():
    return jsonify({"authenticated": bool(session.get("admin"))})


@auth_bp.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))
    expected = str(current_app.config["ADMIN_PASSWORD"])
    if hmac.compare_digest(password, expected):
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid password"}), 401


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.pop("admin", None)
    return jsonify({"ok": True})
