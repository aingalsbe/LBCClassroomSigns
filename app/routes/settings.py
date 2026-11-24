from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, jsonify, request

from app.db import bump_state_version, get_db, write_transaction
from app.routes.auth import require_admin
from app.services.schedule import get_settings_dict


settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/api/settings", methods=["GET"])
def get_settings():
    db = get_db()
    return jsonify(get_settings_dict(db, default_tz=current_app.config["CHURCH_TIMEZONE"]))


@settings_bp.route("/api/settings", methods=["PATCH"])
@require_admin
def update_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    updates = {}
    if "theme" in body:
        theme = str(body["theme"] or "").strip().lower()
        if theme not in ("dark", "light"):
            return jsonify({"error": "Theme must be 'dark' or 'light'"}), 400
        updates["theme"] = theme

    if "timezone" in body:
        tz = str(body["timezone"] or "").strip()
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            return jsonify({"error": f"Invalid IANA timezone: {tz!r}"}), 400
        updates["timezone"] = tz

    db = get_db()
    with write_transaction(db):
        for key, value in updates.items():
            db.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        state_version = bump_state_version(db)

    return jsonify(
        {
            "ok": True,
            "settings": get_settings_dict(db, default_tz=current_app.config["CHURCH_TIMEZONE"]),
            "stateVersion": state_version,
        }
    )
