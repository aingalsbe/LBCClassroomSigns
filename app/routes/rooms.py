import sqlite3

from flask import Blueprint, jsonify, request

from app.db import bump_state_version, get_db, utc_now_iso, write_transaction
from app.routes.auth import require_admin
from app.services.audit import log_audit
from app.services.schedule import natural_sort_key, resolve_age_group_id, serialize_room


rooms_bp = Blueprint("rooms", __name__)


@rooms_bp.route("/api/rooms", methods=["GET"])
def list_rooms():
    db = get_db()
    rows = db.execute("SELECT * FROM rooms").fetchall()
    rooms = sorted((serialize_room(r) for r in rows), key=lambda r: natural_sort_key(r["id"]))
    return jsonify({"rooms": rooms})


@rooms_bp.route("/api/rooms", methods=["POST"])
@require_admin
def create_room():
    body = request.get_json(silent=True) or {}
    room_id = str(body.get("id") or "").strip()
    if not room_id or len(room_id) > 40 or "/" in room_id:
        return jsonify({"error": "Room id is required (max 40 chars, cannot contain '/')"}), 400

    name = str(body.get("name") or f"Room {room_id}").strip()[:120]
    note = str(body.get("note") or "").strip()[:240]

    db = get_db()
    default_ag = body.get("defaultAgeGroupId")
    override_ag = body.get("overrideAgeGroupId")

    if default_ag is not None and str(default_ag).strip() != "":
        resolved = resolve_age_group_id(db, str(default_ag))
        if not resolved:
            return jsonify({"error": f"Unknown defaultAgeGroupId: {default_ag!r}"}), 400
        default_ag = resolved
    else:
        default_ag = None

    if override_ag is not None and str(override_ag).strip() != "":
        resolved = resolve_age_group_id(db, str(override_ag))
        if not resolved:
            return jsonify({"error": f"Unknown overrideAgeGroupId: {override_ag!r}"}), 400
        override_ag = resolved
    else:
        override_ag = None

    now = utc_now_iso()
    try:
        with write_transaction(db):
            db.execute(
                """
                INSERT INTO rooms (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (room_id, name, note, default_ag, override_ag, now),
            )
            state_version = bump_state_version(db)
            log_audit(db, action="room_created", target_type="room", target_id=room_id, details={"name": name})
            row = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Room '{room_id}' already exists"}), 409

    return jsonify({"ok": True, "room": serialize_room(row), "stateVersion": state_version}), 201


@rooms_bp.route("/api/rooms/<room_id>", methods=["PATCH"])
@require_admin
def update_room(room_id: str):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Room '{room_id}' not found"}), 404

        expected_version = body.get("version")
        if expected_version is not None and int(expected_version) != existing["version"]:
            return (
                jsonify(
                    {
                        "error": "Room was modified by another session. Refreshing latest data.",
                        "code": "VERSION_CONFLICT",
                        "current": serialize_room(existing),
                    }
                ),
                409,
            )

        name = existing["name"]
        if "name" in body:
            name = str(body["name"] or "").strip()[:120]
            if not name:
                return jsonify({"error": "Room name cannot be empty"}), 400

        note = existing["note"]
        if "note" in body:
            note = str(body["note"] or "").strip()[:240]

        default_ag = existing["default_age_group_id"]
        if "defaultAgeGroupId" in body:
            val = body["defaultAgeGroupId"]
            if val is None or str(val).strip() == "":
                default_ag = None
            else:
                resolved = resolve_age_group_id(db, str(val))
                if not resolved:
                    return jsonify({"error": f"Unknown defaultAgeGroupId: {val!r}"}), 400
                default_ag = resolved

        override_ag = existing["override_age_group_id"]
        if "overrideAgeGroupId" in body:
            val = body["overrideAgeGroupId"]
            if val is None or str(val).strip() == "":
                override_ag = None
            else:
                resolved = resolve_age_group_id(db, str(val))
                if not resolved:
                    return jsonify({"error": f"Unknown overrideAgeGroupId: {val!r}"}), 400
                override_ag = resolved

        now = utc_now_iso()
        new_version = existing["version"] + 1
        db.execute(
            """
            UPDATE rooms
            SET name = ?, note = ?, default_age_group_id = ?, override_age_group_id = ?,
                version = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, note, default_ag, override_ag, new_version, now, room_id),
        )
        state_version = bump_state_version(db)
        log_audit(
            db,
            action="room_updated",
            target_type="room",
            target_id=room_id,
            details={"fields": sorted(k for k in body.keys() if k != "version")},
        )
        updated = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()

    return jsonify({"ok": True, "room": serialize_room(updated), "stateVersion": state_version})


@rooms_bp.route("/api/rooms/<room_id>", methods=["DELETE"])
@require_admin
def delete_room(room_id: str):
    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT id FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Room '{room_id}' not found"}), 404

        affected_classes = db.execute(
            "SELECT COUNT(*) FROM classes WHERE room_id = ?", (room_id,)
        ).fetchone()[0]
        # Foreign key ON DELETE SET NULL automatically keeps classes intact as unassigned
        db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        state_version = bump_state_version(db)
        log_audit(
            db,
            action="room_deleted",
            target_type="room",
            target_id=room_id,
            details={"unassignedClassCount": affected_classes},
        )

    return jsonify(
        {
            "ok": True,
            "deletedRoomId": room_id,
            "unassignedClassCount": affected_classes,
            "stateVersion": state_version,
        }
    )


@rooms_bp.route("/api/rooms/clear-notices", methods=["POST"])
@require_admin
def clear_all_notices():
    db = get_db()
    now = utc_now_iso()
    with write_transaction(db):
        cleared = db.execute("SELECT COUNT(*) FROM rooms WHERE TRIM(note) != ''").fetchone()[0]
        if cleared > 0:
            db.execute(
                "UPDATE rooms SET note = '', version = version + 1, updated_at = ? WHERE TRIM(note) != ''",
                (now,),
            )
            state_version = bump_state_version(db)
            log_audit(
                db,
                action="room_notices_cleared",
                target_type="room",
                target_id="all",
                details={"clearedCount": cleared},
            )
        else:
            from app.db import get_state_version

            state_version = get_state_version(db)

    return jsonify({"ok": True, "clearedCount": cleared, "stateVersion": state_version})
