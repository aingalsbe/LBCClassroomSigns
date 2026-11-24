from datetime import datetime, timedelta, timezone
import json
import secrets
import sqlite3

from app.db import bump_state_version, utc_now_iso, write_transaction
from app.services.audit import log_audit


STAGED_TTL_MINUTES = 30
MAX_SNAPSHOTS_KEPT = 10


def _capture_snapshot_payload(conn: sqlite3.Connection) -> dict:
    rooms = [dict(r) for r in conn.execute("SELECT * FROM rooms ORDER BY id ASC").fetchall()]
    classes = [dict(c) for c in conn.execute("SELECT * FROM classes ORDER BY id ASC").fetchall()]
    return {"rooms": rooms, "classes": classes}


def _prune_old_snapshots(conn: sqlite3.Connection):
    conn.execute(
        """
        DELETE FROM import_snapshots
        WHERE id NOT IN (
            SELECT id FROM import_snapshots ORDER BY id DESC LIMIT ?
        )
        """,
        (MAX_SNAPSHOTS_KEPT,),
    )


def save_staged_import(
    conn: sqlite3.Connection,
    user_id: int,
    preview: dict,
) -> str:
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=STAGED_TTL_MINUTES)

    payload = {
        "rooms": preview["rooms"],
        "classes": preview["classes"],
    }

    with write_transaction(conn):
        conn.execute("DELETE FROM staged_imports WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            """
            INSERT INTO staged_imports
                (token, user_id, filename, source_type, payload_json, summary_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                user_id,
                preview["filename"],
                preview["sourceType"],
                json.dumps(payload, ensure_ascii=False),
                json.dumps(preview["summary"], ensure_ascii=False),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
    return token


def commit_staged_import(
    conn: sqlite3.Connection,
    token: str,
    user_id: int,
    actor_ip: str,
    mode: str = "replace",
    preserve_room_notices: bool = True,
    preserve_room_overrides: bool = True,
    preserve_admin_classes: bool = True,
) -> dict:
    if mode not in ("replace", "merge"):
        raise ValueError("Import mode must be 'replace' or 'merge'.")

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()

    with write_transaction(conn):
        row = conn.execute(
            "SELECT * FROM staged_imports WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise LookupError("Preview token not found or already used. Please upload the file again.")
        if row["user_id"] != user_id:
            raise PermissionError("Preview token belongs to another user session.")
        if row["expires_at"] < now_iso:
            conn.execute("DELETE FROM staged_imports WHERE token = ?", (token,))
            raise LookupError("Preview has expired. Please upload the file again.")

        staged_payload = json.loads(row["payload_json"])
        staged_summary = json.loads(row["summary_json"])
        incoming_rooms = staged_payload["rooms"]
        incoming_classes = staged_payload["classes"]

        # Verify all referenced age groups still exist
        existing_age_ids = {r["id"] for r in conn.execute("SELECT id FROM age_groups").fetchall()}
        for cls in incoming_classes:
            if cls["ageGroupId"] not in existing_age_ids:
                raise ValueError(
                    f"Age group '{cls['ageGroupId']}' was removed since preview. Please re-upload."
                )

        # Capture pre-import snapshot for one-click rollback
        pre_state = _capture_snapshot_payload(conn)
        snap_cur = conn.execute(
            """
            INSERT INTO import_snapshots
                (filename, source_type, summary_json, payload_json, created_by_user_id, created_by_ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["filename"],
                row["source_type"],
                json.dumps(
                    {
                        "action": f"pre_import_{mode}",
                        "incomingRooms": staged_summary.get("roomsTotal", len(incoming_rooms)),
                        "incomingClasses": staged_summary.get("classesTotal", len(incoming_classes)),
                        "previousRooms": len(pre_state["rooms"]),
                        "previousClasses": len(pre_state["classes"]),
                    }
                ),
                json.dumps(pre_state, ensure_ascii=False),
                user_id,
                actor_ip,
                now_iso,
            ),
        )
        snapshot_id = snap_cur.lastrowid
        _prune_old_snapshots(conn)

        existing_rooms = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT * FROM rooms").fetchall()
        }

        if mode == "replace":
            preserved_admin = []
            if preserve_admin_classes:
                preserved_admin = [
                    dict(c) for c in conn.execute("SELECT * FROM classes WHERE source = 'admin'").fetchall()
                ]

            conn.execute("DELETE FROM classes")
            conn.execute("DELETE FROM rooms")

            for rid, rdata in incoming_rooms.items():
                prev = existing_rooms.get(rid, {})
                note = rdata.get("note", "")
                if preserve_room_notices and prev.get("note") and not note:
                    note = prev["note"]
                override_ag = prev.get("override_age_group_id") if preserve_room_overrides else None
                if override_ag and override_ag not in existing_age_ids:
                    override_ag = None
                conn.execute(
                    """
                    INSERT INTO rooms (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (rid, rdata["name"], note, rdata.get("defaultAgeGroupId"), override_ag, now_iso),
                )

            for cls in incoming_classes:
                conn.execute(
                    """
                    INSERT INTO classes
                        (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upload', 1, ?)
                    """,
                    (
                        cls["ageGroupId"],
                        cls["roomId"],
                        cls["day"],
                        cls.get("date"),
                        cls["start"],
                        cls["end"],
                        cls["title"],
                        cls.get("topic", ""),
                        cls.get("leader", ""),
                        1 if cls.get("cancelled") else 0,
                        now_iso,
                    ),
                )

            # Re-insert preserved admin-created/edited classes (keeping them unassigned if their room disappeared)
            for ac in preserved_admin:
                target_room = ac["room_id"] if ac["room_id"] in incoming_rooms else None
                conn.execute(
                    """
                    INSERT INTO classes
                        (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admin', 1, ?)
                    """,
                    (
                        ac["age_group_id"],
                        target_room,
                        ac["day"],
                        ac["date"],
                        ac["start_time"],
                        ac["end_time"],
                        ac["title"],
                        ac["topic"],
                        ac["leader"],
                        ac["cancelled"],
                        ac["updated_at"],
                    ),
                )
        else:
            # Merge mode: upsert only rooms present in the upload and replace non-admin classes for those rooms
            for rid, rdata in incoming_rooms.items():
                prev = existing_rooms.get(rid)
                if prev:
                    note = rdata.get("note", "")
                    if preserve_room_notices and prev.get("note") and not note:
                        note = prev["note"]
                    override_ag = prev.get("override_age_group_id") if preserve_room_overrides else None
                    conn.execute(
                        """
                        UPDATE rooms
                        SET name = ?, note = ?, default_age_group_id = ?, override_age_group_id = ?,
                            version = version + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (rdata["name"], note, rdata.get("defaultAgeGroupId"), override_ag, now_iso, rid),
                    )
                    if preserve_admin_classes:
                        conn.execute("DELETE FROM classes WHERE room_id = ? AND source != 'admin'", (rid,))
                    else:
                        conn.execute("DELETE FROM classes WHERE room_id = ?", (rid,))
                else:
                    conn.execute(
                        """
                        INSERT INTO rooms (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                        VALUES (?, ?, ?, ?, NULL, 1, ?)
                        """,
                        (rid, rdata["name"], rdata.get("note", ""), rdata.get("defaultAgeGroupId"), now_iso),
                    )

            for cls in incoming_classes:
                conn.execute(
                    """
                    INSERT INTO classes
                        (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upload', 1, ?)
                    """,
                    (
                        cls["ageGroupId"],
                        cls["roomId"],
                        cls["day"],
                        cls.get("date"),
                        cls["start"],
                        cls["end"],
                        cls["title"],
                        cls.get("topic", ""),
                        cls.get("leader", ""),
                        1 if cls.get("cancelled") else 0,
                        now_iso,
                    ),
                )

        conn.execute("DELETE FROM staged_imports WHERE token = ?", (token,))
        state_version = bump_state_version(conn)
        log_audit(
            conn,
            action="schedule_imported",
            target_type="snapshot",
            target_id=str(snapshot_id),
            details={
                "filename": row["filename"],
                "sourceType": row["source_type"],
                "mode": mode,
                "roomsImported": len(incoming_rooms),
                "classesImported": len(incoming_classes),
                "preserveRoomNotices": preserve_room_notices,
                "preserveRoomOverrides": preserve_room_overrides,
                "preserveAdminClasses": preserve_admin_classes,
            },
        )

    return {
        "snapshotId": snapshot_id,
        "stateVersion": state_version,
        "filename": row["filename"],
        "mode": mode,
    }


def list_import_snapshots(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit or 10), 50))
    rows = conn.execute(
        """
        SELECT s.id, s.filename, s.source_type, s.summary_json, s.created_by_ip, s.created_at,
               u.username, u.display_name
        FROM import_snapshots s
        LEFT JOIN users u ON u.id = s.created_by_user_id
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items = []
    for r in rows:
        try:
            summary = json.loads(r["summary_json"] or "{}")
        except json.JSONDecodeError:
            summary = {}
        items.append(
            {
                "id": r["id"],
                "filename": r["filename"],
                "sourceType": r["source_type"],
                "summary": summary,
                "createdBy": r["username"] or "system",
                "createdByName": r["display_name"] or r["username"] or "System",
                "createdByIp": r["created_by_ip"],
                "createdAt": r["created_at"],
            }
        )
    return items


def rollback_to_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: int,
    user_id: int,
    actor_ip: str,
) -> dict:
    now_iso = utc_now_iso()
    with write_transaction(conn):
        snap = conn.execute("SELECT * FROM import_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if not snap:
            raise LookupError(f"Snapshot #{snapshot_id} not found.")

        target_state = json.loads(snap["payload_json"])
        valid_age_ids = {r["id"] for r in conn.execute("SELECT id FROM age_groups").fetchall()}
        default_age_row = conn.execute(
            "SELECT id FROM age_groups ORDER BY is_default DESC, sort_order ASC LIMIT 1"
        ).fetchone()
        fallback_age_id = default_age_row["id"] if default_age_row else "adults"

        # Save current state as a safety snapshot before rolling back
        pre_rollback = _capture_snapshot_payload(conn)
        conn.execute(
            """
            INSERT INTO import_snapshots
                (filename, source_type, summary_json, payload_json, created_by_user_id, created_by_ip, created_at)
            VALUES (?, 'rollback', ?, ?, ?, ?, ?)
            """,
            (
                f"Before rollback to #{snapshot_id} ({snap['filename']})",
                json.dumps(
                    {
                        "action": "pre_rollback",
                        "previousRooms": len(pre_rollback["rooms"]),
                        "previousClasses": len(pre_rollback["classes"]),
                    }
                ),
                json.dumps(pre_rollback, ensure_ascii=False),
                user_id,
                actor_ip,
                now_iso,
            ),
        )
        _prune_old_snapshots(conn)

        conn.execute("DELETE FROM classes")
        conn.execute("DELETE FROM rooms")

        restored_room_ids = set()
        for r in target_state.get("rooms", []):
            def_ag = r.get("default_age_group_id")
            if def_ag and def_ag not in valid_age_ids:
                def_ag = fallback_age_id
            ovr_ag = r.get("override_age_group_id")
            if ovr_ag and ovr_ag not in valid_age_ids:
                ovr_ag = None
            conn.execute(
                """
                INSERT INTO rooms (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (r["id"], r["name"], r.get("note", ""), def_ag, ovr_ag, now_iso),
            )
            restored_room_ids.add(r["id"])

        for c in target_state.get("classes", []):
            ag_id = c.get("age_group_id")
            if ag_id not in valid_age_ids:
                ag_id = fallback_age_id
            rid = c.get("room_id")
            if rid and rid not in restored_room_ids:
                rid = None
            conn.execute(
                """
                INSERT INTO classes
                    (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    ag_id,
                    rid,
                    c["day"],
                    c.get("date"),
                    c["start_time"],
                    c["end_time"],
                    c["title"],
                    c.get("topic", ""),
                    c.get("leader", ""),
                    1 if c.get("cancelled") else 0,
                    c.get("source", "upload"),
                    now_iso,
                ),
            )

        state_version = bump_state_version(conn)
        log_audit(
            conn,
            action="schedule_rolled_back",
            target_type="snapshot",
            target_id=str(snapshot_id),
            details={
                "restoredFromFilename": snap["filename"],
                "restoredRooms": len(restored_room_ids),
                "restoredClasses": len(target_state.get("classes", [])),
            },
        )

    return {
        "restoredSnapshotId": snapshot_id,
        "stateVersion": state_version,
        "restoredRooms": len(restored_room_ids),
        "restoredClasses": len(target_state.get("classes", [])),
    }
