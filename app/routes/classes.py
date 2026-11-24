from flask import Blueprint, jsonify, request

from app.db import bump_state_version, get_db, utc_now_iso, write_transaction
from app.routes.auth import require_admin
from app.services.audit import log_audit
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
        log_audit(
            db,
            action="class_created",
            target_type="class",
            target_id=str(class_id),
            details={"roomId": room_id, "ageGroupId": age_group_id, "title": title, "day": day},
        )
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
        log_audit(
            db,
            action="class_updated",
            target_type="class",
            target_id=str(class_id),
            details={"title": title, "fields": sorted(k for k in body.keys() if k != "version"), "cancelled": bool(cancelled)},
        )
        updated = db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()

    return jsonify({"ok": True, "class": serialize_class(updated), "stateVersion": state_version})


@classes_bp.route("/api/classes/<int:class_id>", methods=["DELETE"])
@require_admin
def delete_class(class_id: int):
    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT id, title, room_id FROM classes WHERE id = ?", (class_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Class #{class_id} not found"}), 404
        db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
        state_version = bump_state_version(db)
        log_audit(
            db,
            action="class_deleted",
            target_type="class",
            target_id=str(class_id),
            details={"title": existing["title"], "roomId": existing["room_id"]},
        )

    return jsonify({"ok": True, "deletedClassId": class_id, "stateVersion": state_version})


@classes_bp.route("/api/classes/swap", methods=["POST"])
@require_admin
def swap_or_move_class():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    class_id = body.get("classId")
    target_room_id = body.get("targetRoomId")
    exchange_id = body.get("exchangeWithClassId")

    if not class_id:
        return jsonify({"error": "classId is required"}), 400
    target_room_id = str(target_room_id).strip() if (target_room_id is not None and str(target_room_id).strip() != "") else None

    db = get_db()
    now = utc_now_iso()
    try:
        with write_transaction(db):
            cls_a = db.execute("SELECT * FROM classes WHERE id = ?", (int(class_id),)).fetchone()
            if not cls_a:
                return jsonify({"error": f"Class #{class_id} not found"}), 404

            if exchange_id is not None:
                cls_b = db.execute("SELECT * FROM classes WHERE id = ?", (int(exchange_id),)).fetchone()
                if not cls_b:
                    return jsonify({"error": f"Exchange class #{exchange_id} not found"}), 404
                if cls_a["id"] == cls_b["id"]:
                    return jsonify({"error": "Cannot swap a class with itself"}), 400

                room_a, room_b = cls_a["room_id"], cls_b["room_id"]
                if room_a == room_b:
                    return jsonify({"error": "Both classes are already in the same room"}), 400

                # Temporarily unassign both inside the transaction to validate clean overlaps against other classes
                db.execute("UPDATE classes SET room_id = NULL WHERE id IN (?, ?)", (cls_a["id"], cls_b["id"]))

                if not cls_a["cancelled"]:
                    c1 = find_overlapping_class(
                        db, room_b, cls_a["day"], cls_a["start_time"], cls_a["end_time"], date=cls_a["date"]
                    )
                    if c1:
                        raise ValueError(
                            f"Cannot swap: '{cls_a['title']}' overlaps '{c1['title']}' ({c1['start_time']}–{c1['end_time']}) in Room {room_b}."
                        )
                if not cls_b["cancelled"]:
                    c2 = find_overlapping_class(
                        db, room_a, cls_b["day"], cls_b["start_time"], cls_b["end_time"], date=cls_b["date"]
                    )
                    if c2:
                        raise ValueError(
                            f"Cannot swap: '{cls_b['title']}' overlaps '{c2['title']}' ({c2['start_time']}–{c2['end_time']}) in Room {room_a}."
                        )

                db.execute(
                    "UPDATE classes SET room_id = ?, source = 'admin', version = version + 1, updated_at = ? WHERE id = ?",
                    (room_b, now, cls_a["id"]),
                )
                db.execute(
                    "UPDATE classes SET room_id = ?, source = 'admin', version = version + 1, updated_at = ? WHERE id = ?",
                    (room_a, now, cls_b["id"]),
                )
                state_version = bump_state_version(db)
                log_audit(
                    db,
                    action="classes_swapped",
                    target_type="class",
                    target_id=f"{cls_a['id']}<->{cls_b['id']}",
                    details={
                        "classA": {"id": cls_a["id"], "title": cls_a["title"], "from": room_a, "to": room_b},
                        "classB": {"id": cls_b["id"], "title": cls_b["title"], "from": room_b, "to": room_a},
                    },
                )
                updated_a = db.execute("SELECT * FROM classes WHERE id = ?", (cls_a["id"],)).fetchone()
                updated_b = db.execute("SELECT * FROM classes WHERE id = ?", (cls_b["id"],)).fetchone()
                return jsonify(
                    {
                        "ok": True,
                        "mode": "swap",
                        "classes": [serialize_class(updated_a), serialize_class(updated_b)],
                        "stateVersion": state_version,
                    }
                )

            # Single move to target_room_id (or unassigned if None)
            if target_room_id is not None:
                if not db.execute("SELECT 1 FROM rooms WHERE id = ?", (target_room_id,)).fetchone():
                    return jsonify({"error": f"Room '{target_room_id}' does not exist"}), 404
                if not cls_a["cancelled"]:
                    conflict = find_overlapping_class(
                        db,
                        target_room_id,
                        cls_a["day"],
                        cls_a["start_time"],
                        cls_a["end_time"],
                        date=cls_a["date"],
                        exclude_class_id=cls_a["id"],
                    )
                    if conflict:
                        return (
                            jsonify(
                                {
                                    "error": (
                                        f"Room {target_room_id} is busy on {cls_a['day']}: "
                                        f"'{conflict['title']}' ({conflict['start_time']}–{conflict['end_time']}). "
                                        "Use Exchange to swap rooms."
                                    ),
                                    "code": "SCHEDULE_OVERLAP",
                                    "conflict": conflict,
                                }
                            ),
                            409,
                        )

            prev_room = cls_a["room_id"]
            db.execute(
                "UPDATE classes SET room_id = ?, source = 'admin', version = version + 1, updated_at = ? WHERE id = ?",
                (target_room_id, now, cls_a["id"]),
            )
            state_version = bump_state_version(db)
            log_audit(
                db,
                action="class_moved",
                target_type="class",
                target_id=str(cls_a["id"]),
                details={"title": cls_a["title"], "from": prev_room, "to": target_room_id},
            )
            updated = db.execute("SELECT * FROM classes WHERE id = ?", (cls_a["id"],)).fetchone()
            return jsonify(
                {
                    "ok": True,
                    "mode": "move",
                    "class": serialize_class(updated),
                    "stateVersion": state_version,
                }
            )
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "SCHEDULE_OVERLAP"}), 409
