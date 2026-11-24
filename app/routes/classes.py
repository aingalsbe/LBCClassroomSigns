from flask import Blueprint, jsonify, request

from app.db import bump_state_version, get_db, utc_now_iso, write_transaction
from app.routes.auth import require_admin
from app.services.schedule import (
    VALID_DAYS,
    find_overlapping_class,
    natural_sort_key,
    normalize_date,
    normalize_day,
    normalize_time,
    resolve_age_group_id,
    serialize_class,
)


classes_bp = Blueprint("classes", __name__)


@classes_bp.route("/api/classes", methods=["GET"])
def list_classes():
    db = get_db()
    room_filter = request.args.get("room")
    day_filter = request.args.get("day")
    age_filter = request.args.get("ageGroup")
    unassigned = request.args.get("unassigned")

    clauses = []
    params = []
    if unassigned in ("1", "true", "yes"):
        clauses.append("room_id IS NULL")
    elif room_filter:
        clauses.append("room_id = ?")
        params.append(room_filter)
    if day_filter:
        day_norm = normalize_day(day_filter) or day_filter
        clauses.append("day = ?")
        params.append(day_norm)
    if age_filter:
        ag_id = resolve_age_group_id(db, age_filter) or age_filter
        clauses.append("age_group_id = ?")
        params.append(ag_id)

    sql = "SELECT * FROM classes"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    day_order = {d: i for i, d in enumerate(VALID_DAYS)}
    rows = db.execute(sql, params).fetchall()
    classes = sorted(
        (serialize_class(r) for r in rows),
        key=lambda c: (
            natural_sort_key(c["roomId"] or "zzzz"),
            day_order.get(c["day"], 99),
            c["start"],
            c["id"],
        ),
    )
    return jsonify({"classes": classes})


@classes_bp.route("/api/classes", methods=["POST"])
@require_admin
def create_class():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    db = get_db()

    raw_age = body.get("ageGroupId") or body.get("ageGroup")
    age_group_id = resolve_age_group_id(db, str(raw_age or ""))
    if not age_group_id:
        return jsonify({"error": "A valid ageGroupId is required for every class"}), 400

    raw_room = body.get("roomId") if "roomId" in body else body.get("room")
    room_id = str(raw_room).strip() if (raw_room is not None and str(raw_room).strip() != "") else None

    day = normalize_day(str(body.get("day") or ""))
    if not day:
        return jsonify({"error": f"Invalid day: {body.get('day')!r}"}), 400

    start = normalize_time(body.get("start"))
    end = normalize_time(body.get("end"))
    if not start or not end or start >= end:
        return jsonify({"error": "Valid start and end times are required (start must be before end)"}), 400

    raw_date = body.get("date")
    date_val = normalize_date(raw_date) if raw_date else None
    if raw_date and not date_val:
        return jsonify({"error": f"Invalid date format (expected YYYY-MM-DD): {raw_date!r}"}), 400

    title = str(body.get("title") or "").strip()[:140]
    if not title:
        return jsonify({"error": "Class title is required"}), 400

    topic = str(body.get("topic") or "").strip()[:160]
    leader = str(body.get("leader") or "").strip()[:200]
    cancelled = 1 if body.get("cancelled") else 0
    force_overlap = bool(body.get("forceOverlap", False))

    now = utc_now_iso()
    with write_transaction(db):
        if room_id is not None:
            room_exists = db.execute("SELECT id FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if not room_exists:
                return jsonify({"error": f"Room '{room_id}' does not exist"}), 400

        if not cancelled and not force_overlap:
            conflict = find_overlapping_class(db, room_id, day, start, end, date=date_val)
            if conflict:
                return (
                    jsonify(
                        {
                            "error": (
                                f"Time overlap in Room {room_id} on {day}: "
                                f"conflicts with '{conflict['title']}' ({conflict['start_time']}–{conflict['end_time']})"
                            ),
                            "code": "SCHEDULE_OVERLAP",
                            "conflict": conflict,
                        }
                    ),
                    409,
                )

        cur = db.execute(
            """
            INSERT INTO classes
                (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admin', 1, ?)
            """,
            (age_group_id, room_id, day, date_val, start, end, title, topic, leader, cancelled, now),
        )
        class_id = cur.lastrowid
        state_version = bump_state_version(db)
        row = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()

    return jsonify({"ok": True, "class": serialize_class(row), "stateVersion": state_version}), 201


@classes_bp.route("/api/classes/<int:class_id>", methods=["PATCH"])
@require_admin
def update_class(class_id: int):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Class #{class_id} not found"}), 404

        expected_version = body.get("version")
        if expected_version is not None and int(expected_version) != existing["version"]:
            return (
                jsonify(
                    {
                        "error": "Class was modified by another session. Refreshing latest data.",
                        "code": "VERSION_CONFLICT",
                        "current": serialize_class(existing),
                    }
                ),
                409,
            )

        age_group_id = existing["age_group_id"]
        if "ageGroupId" in body or "ageGroup" in body:
            raw_age = body.get("ageGroupId") if "ageGroupId" in body else body.get("ageGroup")
            resolved = resolve_age_group_id(db, str(raw_age or ""))
            if not resolved:
                return jsonify({"error": "Every class requires a valid ageGroupId"}), 400
            age_group_id = resolved

        room_id = existing["room_id"]
        if "roomId" in body or "room" in body:
            raw_room = body.get("roomId") if "roomId" in body else body.get("room")
            if raw_room is None or str(raw_room).strip() == "":
                room_id = None
            else:
                room_id = str(raw_room).strip()
                room_exists = db.execute("SELECT id FROM rooms WHERE id = ?", (room_id,)).fetchone()
                if not room_exists:
                    return jsonify({"error": f"Room '{room_id}' does not exist"}), 400

        day = existing["day"]
        if "day" in body:
            day = normalize_day(str(body["day"] or ""))
            if not day:
                return jsonify({"error": f"Invalid day: {body['day']!r}"}), 400

        date_val = existing["date"]
        if "date" in body:
            raw_date = body["date"]
            if raw_date is None or str(raw_date).strip() == "":
                date_val = None
            else:
                date_val = normalize_date(raw_date)
                if not date_val:
                    return jsonify({"error": f"Invalid date: {raw_date!r}"}), 400

        start = existing["start_time"]
        if "start" in body:
            start = normalize_time(body["start"])
            if not start:
                return jsonify({"error": f"Invalid start time: {body['start']!r}"}), 400

        end = existing["end_time"]
        if "end" in body:
            end = normalize_time(body["end"])
            if not end:
                return jsonify({"error": f"Invalid end time: {body['end']!r}"}), 400

        if start >= end:
            return jsonify({"error": "Start time must be earlier than end time"}), 400

        title = existing["title"]
        if "title" in body:
            title = str(body["title"] or "").strip()[:140]
            if not title:
                return jsonify({"error": "Class title cannot be empty"}), 400

        topic = existing["topic"]
        if "topic" in body:
            topic = str(body["topic"] or "").strip()[:160]

        leader = existing["leader"]
        if "leader" in body:
            leader = str(body["leader"] or "").strip()[:200]

        cancelled = existing["cancelled"]
        if "cancelled" in body:
            cancelled = 1 if body["cancelled"] else 0

        force_overlap = bool(body.get("forceOverlap", False))
        schedule_changed = (
            room_id != existing["room_id"]
            or day != existing["day"]
            or date_val != existing["date"]
            or start != existing["start_time"]
            or end != existing["end_time"]
            or (existing["cancelled"] == 1 and cancelled == 0)
        )
        if not cancelled and schedule_changed and not force_overlap:
            conflict = find_overlapping_class(
                db, room_id, day, start, end, date=date_val, exclude_class_id=class_id
            )
            if conflict:
                return (
                    jsonify(
                        {
                            "error": (
                                f"Time overlap in Room {room_id} on {day}: "
                                f"conflicts with '{conflict['title']}' ({conflict['start_time']}–{conflict['end_time']})"
                            ),
                            "code": "SCHEDULE_OVERLAP",
                            "conflict": conflict,
                        }
                    ),
                    409,
                )

        now = utc_now_iso()
        new_version = existing["version"] + 1
        db.execute(
            """
            UPDATE classes
            SET age_group_id = ?, room_id = ?, day = ?, date = ?,
                start_time = ?, end_time = ?, title = ?, topic = ?, leader = ?,
                cancelled = ?, source = 'admin', version = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                age_group_id,
                room_id,
                day,
                date_val,
                start,
                end,
                title,
                topic,
                leader,
                cancelled,
                new_version,
                now,
                class_id,
            ),
        )
        state_version = bump_state_version(db)
        updated = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()

    return jsonify({"ok": True, "class": serialize_class(updated), "stateVersion": state_version})


@classes_bp.route("/api/classes/<int:class_id>", methods=["DELETE"])
@require_admin
def delete_class(class_id: int):
    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT id FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Class #{class_id} not found"}), 404
        db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
        state_version = bump_state_version(db)

    return jsonify({"ok": True, "deletedClassId": class_id, "stateVersion": state_version})
