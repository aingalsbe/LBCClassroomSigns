from flask import Blueprint, current_app, jsonify, make_response, request

from app.db import bump_state_version, get_db, get_state_version, utc_now_iso, write_transaction
from app.routes.auth import require_admin
from app.services.audit import log_audit
from app.services.schedule import (
    build_state_payload,
    normalize_date,
    normalize_day,
    normalize_time,
    resolve_age_group_id,
)


state_bp = Blueprint("state", __name__)


@state_bp.route("/api/health", methods=["GET"])
def health_check():
    db = get_db()
    db.execute("SELECT 1").fetchone()
    journal_mode = db.execute("PRAGMA journal_mode;").fetchone()[0]
    state_version = get_state_version(db)
    return jsonify(
        {
            "ok": True,
            "db": "sqlite",
            "journalMode": journal_mode,
            "stateVersion": state_version,
            "buildId": current_app.config["BUILD_ID"],
        }
    )


@state_bp.route("/api/state", methods=["GET"])
def get_state():
    db = get_db()
    state_version = get_state_version(db)
    build_id = current_app.config["BUILD_ID"]
    etag = f"v{state_version}-{build_id}"

    if request.if_none_match and request.if_none_match.contains(etag):
        resp = make_response("", 304)
        resp.set_etag(etag)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    payload = build_state_payload(
        db,
        build_id=build_id,
        default_tz=current_app.config["CHURCH_TIMEZONE"],
    )
    resp = jsonify(payload)
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@state_bp.route("/api/schedule/replace", methods=["POST"])
@require_admin
def replace_schedule():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid schedule payload"}), 400

    rooms_input = body.get("roomsMap")
    classes_input = body.get("scheduleList")
    preserve_overrides = bool(body.get("preserveRoomOverrides", True))

    if not isinstance(rooms_input, dict) or not isinstance(classes_input, list):
        return jsonify({"error": "Payload must include roomsMap (object) and scheduleList (list)"}), 400
    if not classes_input:
        return jsonify({"error": "scheduleList cannot be empty"}), 400

    db = get_db()
    default_age = db.execute(
        "SELECT id FROM age_groups ORDER BY is_default DESC, sort_order ASC LIMIT 1"
    ).fetchone()
    fallback_age_id = default_age["id"] if default_age else "adults"

    validated_rooms = {}
    for raw_rid, info in rooms_input.items():
        rid = str(raw_rid or "").strip()
        if not rid or len(rid) > 40 or not isinstance(info, dict):
            return jsonify({"error": f"Invalid room entry: {raw_rid!r}"}), 400
        name = str(info.get("name") or f"Room {rid}").strip()[:120]
        note = str(info.get("note") or "").strip()[:240]
        raw_age = info.get("defaultAgeGroupId") or info.get("ageGroup") or ""
        age_id = resolve_age_group_id(db, str(raw_age)) if raw_age else fallback_age_id
        if not age_id:
            age_id = fallback_age_id
        validated_rooms[rid] = {"name": name, "note": note, "default_age_group_id": age_id}

    validated_classes = []
    for idx, item in enumerate(classes_input, start=1):
        if not isinstance(item, dict):
            return jsonify({"error": f"Invalid class row #{idx}"}), 400
        rid = str(item.get("roomId") or item.get("room") or "").strip() or None
        if rid and rid not in validated_rooms:
            validated_rooms[rid] = {
                "name": f"Room {rid}",
                "note": "",
                "default_age_group_id": fallback_age_id,
            }
        day = normalize_day(str(item.get("day") or ""))
        if not day:
            return jsonify({"error": f"Row #{idx}: invalid day {item.get('day')!r}"}), 400
        start = normalize_time(item.get("start"))
        end = normalize_time(item.get("end"))
        if not start or not end or start >= end:
            return jsonify({"error": f"Row #{idx}: invalid time range {item.get('start')!r} - {item.get('end')!r}"}), 400

        raw_date = item.get("date")
        date_val = normalize_date(raw_date) if raw_date else None
        if raw_date and not date_val:
            return jsonify({"error": f"Row #{idx}: invalid date {raw_date!r}"}), 400

        raw_age = item.get("ageGroupId") or item.get("ageGroup")
        if raw_age:
            age_id = resolve_age_group_id(db, str(raw_age)) or fallback_age_id
        elif rid and rid in validated_rooms:
            age_id = validated_rooms[rid]["default_age_group_id"]
        else:
            age_id = fallback_age_id

        title = str(item.get("title") or "").strip()[:140]
        if not title:
            return jsonify({"error": f"Row #{idx}: class title is required"}), 400

        validated_classes.append(
            {
                "room_id": rid,
                "age_group_id": age_id,
                "day": day,
                "date": date_val,
                "start_time": start,
                "end_time": end,
                "title": title,
                "topic": str(item.get("topic") or "").strip()[:160],
                "leader": str(item.get("leader") or "").strip()[:200],
                "cancelled": 1 if item.get("cancelled") else 0,
            }
        )

    now = utc_now_iso()
    with write_transaction(db):
        existing_rooms = {}
        if preserve_overrides:
            for row in db.execute("SELECT id, note, override_age_group_id FROM rooms").fetchall():
                existing_rooms[row["id"]] = {
                    "note": row["note"],
                    "override_age_group_id": row["override_age_group_id"],
                }

        db.execute("DELETE FROM classes")
        db.execute("DELETE FROM rooms")

        for rid, rdata in validated_rooms.items():
            prev = existing_rooms.get(rid, {})
            note = prev.get("note") if (preserve_overrides and prev.get("note") and not rdata["note"]) else rdata["note"]
            override_ag = prev.get("override_age_group_id") if preserve_overrides else None
            db.execute(
                """
                INSERT INTO rooms (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (rid, rdata["name"], note, rdata["default_age_group_id"], override_ag, now),
            )

        for c in validated_classes:
            db.execute(
                """
                INSERT INTO classes
                    (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upload', 1, ?)
                """,
                (
                    c["age_group_id"],
                    c["room_id"],
                    c["day"],
                    c["date"],
                    c["start_time"],
                    c["end_time"],
                    c["title"],
                    c["topic"],
                    c["leader"],
                    c["cancelled"],
                    now,
                ),
            )

        state_version = bump_state_version(db)
        log_audit(
            db,
            action="schedule_replaced",
            target_type="schedule",
            target_id="all",
            details={
                "roomCount": len(validated_rooms),
                "classCount": len(validated_classes),
                "preserveRoomOverrides": preserve_overrides,
            },
        )

    payload = build_state_payload(
        db,
        build_id=current_app.config["BUILD_ID"],
        default_tz=current_app.config["CHURCH_TIMEZONE"],
    )
    return jsonify({"ok": True, "stateVersion": state_version, "state": payload})
