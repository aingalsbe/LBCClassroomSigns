from datetime import datetime, timezone
import json
import re
import sqlite3

from app.db import get_state_version


VALID_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
DAY_ALIASES = {
    "sun": "Sunday",
    "sunday": "Sunday",
    "mon": "Monday",
    "monday": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "tuesday": "Tuesday",
    "wed": "Wednesday",
    "weds": "Wednesday",
    "wednesday": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "thursday": "Thursday",
    "fri": "Friday",
    "friday": "Friday",
    "sat": "Saturday",
    "saturday": "Saturday",
}

TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?:\s*(AM|PM))?$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NATURAL_TOKEN_RE = re.compile(r"(\d+)")


def natural_sort_key(value: str):
    parts = NATURAL_TOKEN_RE.split(str(value or "").strip())
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in parts]


def normalize_day(raw_day: str) -> str | None:
    if not isinstance(raw_day, str):
        return None
    return DAY_ALIASES.get(raw_day.strip().lower())


def normalize_time(raw_time) -> str | None:
    if raw_time is None:
        return None
    if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool):
        if raw_time < 0 or raw_time >= 1.5:
            return None
        total_minutes = round(float(raw_time) * 24 * 60)
        h = (total_minutes // 60) % 24
        m = total_minutes % 60
        return f"{h:02d}:{m:02d}"

    text = str(raw_time).strip()
    match = TIME_RE.match(text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = (match.group(3) or "").upper()

    if minute < 0 or minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
    else:
        if hour < 0 or hour > 23:
            return None
    return f"{hour:02d}:{minute:02d}"


def normalize_date(raw_date) -> str | None:
    if raw_date is None or str(raw_date).strip() == "":
        return None
    text = str(raw_date).strip()
    if not DATE_RE.match(text):
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def resolve_age_group_id(conn: sqlite3.Connection, raw_value: str) -> str | None:
    if not raw_value or not isinstance(raw_value, str):
        return None
    candidate = raw_value.strip().lower()
    rows = conn.execute(
        "SELECT id, label, aliases_json FROM age_groups ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    for row in rows:
        if candidate == row["id"].lower() or candidate == row["label"].lower():
            return row["id"]
    for row in rows:
        try:
            aliases = json.loads(row["aliases_json"] or "[]")
        except json.JSONDecodeError:
            aliases = []
        if any(candidate == str(a).strip().lower() for a in aliases):
            return row["id"]
    return None


def find_overlapping_class(
    conn: sqlite3.Connection,
    room_id: str | None,
    day: str,
    start_time: str,
    end_time: str,
    date: str | None = None,
    exclude_class_id: int | None = None,
):
    if not room_id:
        return None
    params = [room_id, day, end_time, start_time]
    sql = """
        SELECT id, title, start_time, end_time, day, date
        FROM classes
        WHERE room_id = ?
          AND day = ?
          AND cancelled = 0
          AND start_time < ?
          AND end_time > ?
    """
    if date is None:
        sql += " AND date IS NULL"
    else:
        sql += " AND date = ?"
        params.append(date)
    if exclude_class_id is not None:
        sql += " AND id != ?"
        params.append(exclude_class_id)
    sql += " LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def serialize_age_group(row: sqlite3.Row) -> dict:
    try:
        aliases = json.loads(row["aliases_json"] or "[]")
    except json.JSONDecodeError:
        aliases = []
    return {
        "id": row["id"],
        "label": row["label"],
        "bannerBg": row["banner_bg"],
        "bannerText": row["banner_text"],
        "badgeBg": row["badge_bg"],
        "badgeText": row["badge_text"],
        "sortOrder": row["sort_order"],
        "aliases": aliases,
        "isDefault": bool(row["is_default"]),
        "updatedAt": row["updated_at"],
    }


def serialize_room(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "note": row["note"],
        "defaultAgeGroupId": row["default_age_group_id"],
        "overrideAgeGroupId": row["override_age_group_id"],
        # Legacy-friendly alias for UI convenience
        "ageGroup": row["override_age_group_id"] or row["default_age_group_id"],
        "version": row["version"],
        "updatedAt": row["updated_at"],
    }


def serialize_class(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "room": row["room_id"],
        "roomId": row["room_id"],
        "ageGroupId": row["age_group_id"],
        "day": row["day"],
        "date": row["date"],
        "start": row["start_time"],
        "end": row["end_time"],
        "title": row["title"],
        "topic": row["topic"],
        "leader": row["leader"],
        "cancelled": bool(row["cancelled"]),
        "source": row["source"],
        "version": row["version"],
        "updatedAt": row["updated_at"],
    }


def get_settings_dict(conn: sqlite3.Connection, default_tz: str = "America/Chicago") -> dict:
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    raw = {r["key"]: r["value"] for r in rows}
    return {
        "theme": raw.get("theme", "dark"),
        "timezone": raw.get("timezone", default_tz),
    }


def build_state_payload(conn: sqlite3.Connection, build_id: str, default_tz: str = "America/Chicago") -> dict:
    age_rows = conn.execute(
        "SELECT * FROM age_groups ORDER BY sort_order ASC, label ASC"
    ).fetchall()
    age_groups = [serialize_age_group(r) for r in age_rows]
    age_groups_map = {ag["id"]: ag for ag in age_groups}

    room_rows = conn.execute("SELECT * FROM rooms").fetchall()
    rooms = sorted((serialize_room(r) for r in room_rows), key=lambda r: natural_sort_key(r["id"]))
    rooms_map = {r["id"]: r for r in rooms}

    day_order = {d: i for i, d in enumerate(VALID_DAYS)}
    class_rows = conn.execute("SELECT * FROM classes").fetchall()
    classes = sorted(
        (serialize_class(r) for r in class_rows),
        key=lambda c: (
            natural_sort_key(c["roomId"] or "zzzz"),
            day_order.get(c["day"], 99),
            c["start"],
            c["id"],
        ),
    )

    state_version = get_state_version(conn)
    settings = get_settings_dict(conn, default_tz=default_tz)

    return {
        "stateVersion": state_version,
        "buildId": build_id,
        "serverNow": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "ageGroups": age_groups,
        "ageGroupsMap": age_groups_map,
        "rooms": rooms,
        "roomsMap": rooms_map,
        "scheduleList": classes,
    }
