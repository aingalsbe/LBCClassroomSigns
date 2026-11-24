from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import shutil
import sqlite3
import time

from flask import current_app, g


SCHEMA_VERSION = 2

DEFAULT_AGE_GROUPS = [
    {
        "id": "kids",
        "label": "Kids",
        "banner_bg": "#0284c7",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(14, 165, 233, 0.2)",
        "badge_text": "#7dd3fc",
        "sort_order": 1,
        "aliases": ["Kids", "Children", "Elementary", "Nursery", "Preschool"],
        "is_default": 0,
    },
    {
        "id": "teen",
        "label": "Teen",
        "banner_bg": "#d97706",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(245, 158, 11, 0.2)",
        "badge_text": "#fcd34d",
        "sort_order": 2,
        "aliases": ["Teen", "Teens", "Youth", "Students", "Middle School", "High School"],
        "is_default": 0,
    },
    {
        "id": "college",
        "label": "College",
        "banner_bg": "#0f766e",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(20, 184, 166, 0.2)",
        "badge_text": "#5eead4",
        "sort_order": 3,
        "aliases": ["College", "Young Adults", "Young Adult"],
        "is_default": 0,
    },
    {
        "id": "adults",
        "label": "Adults",
        "banner_bg": "#4338ca",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(99, 102, 241, 0.2)",
        "badge_text": "#a5b4fc",
        "sort_order": 4,
        "aliases": ["Adults", "Adult", "Family", "Young Marrieds", "Empty Nesters"],
        "is_default": 1,
    },
    {
        "id": "seniors",
        "label": "Seniors",
        "banner_bg": "#047857",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(16, 185, 129, 0.2)",
        "badge_text": "#6ee7b7",
        "sort_order": 5,
        "aliases": ["Seniors", "Senior Adults", "Senior Ladies", "Senior Men"],
        "is_default": 0,
    },
    {
        "id": "special",
        "label": "Special Event",
        "banner_bg": "#7e22ce",
        "banner_text": "#ffffff",
        "badge_bg": "rgba(168, 85, 247, 0.2)",
        "badge_text": "#d8b4fe",
        "sort_order": 6,
        "aliases": ["Special", "Special Event", "Event"],
        "is_default": 0,
    },
]

DEFAULT_SAMPLE_STATE = {
    "roomsMap": {
        "101": {"name": "Room 101 — Chapel", "ageGroup": "Adults", "note": ""},
        "102": {"name": "Room 102 — Youth Loft", "ageGroup": "Teen", "note": ""},
        "204": {"name": "Room 204 — College Lounge", "ageGroup": "College", "note": ""},
        "105": {"name": "Room 105 — Kids Zone", "ageGroup": "Kids", "note": ""},
    },
    "scheduleList": [
        {"room": "101", "day": "Sunday", "start": "09:00", "end": "10:15", "title": "Foundations of Faith", "leader": "Pastor Mark Davis", "cancelled": False},
        {"room": "101", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Men's & Women's Roundtable", "leader": "Sarah Jenkins", "cancelled": False},
        {"room": "102", "day": "Sunday", "start": "09:00", "end": "10:15", "title": "High School Morning Hangout", "leader": "Tyler Brooks", "cancelled": False},
        {"room": "102", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Middle School Small Groups", "leader": "Hannah Cole", "cancelled": False},
        {"room": "204", "day": "Sunday", "start": "09:30", "end": "10:45", "title": "College Coffee & Study", "leader": "Alex Rivera", "cancelled": False},
        {"room": "204", "day": "Sunday", "start": "11:00", "end": "12:15", "title": "Young Adults Discussion", "leader": "Jordan Lee", "cancelled": False},
        {"room": "105", "day": "Sunday", "start": "09:00", "end": "10:30", "title": "Elementary Worship & Crafts", "leader": "Emily Watson", "cancelled": False},
        {"room": "105", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Kids Bible Adventure", "leader": "Emily Watson", "cancelled": False},
        {"room": "101", "day": "Wednesday", "start": "18:00", "end": "19:30", "title": "Midweek Adult Prayer & Study", "leader": "Pastor Mark Davis", "cancelled": False},
        {"room": "102", "day": "Wednesday", "start": "18:15", "end": "19:45", "title": "Wednesday Night Youth Live", "leader": "Tyler Brooks", "cancelled": False},
        {"room": "204", "day": "Wednesday", "start": "18:30", "end": "20:00", "title": "College Midweek Worship", "leader": "Alex Rivera", "cancelled": False},
        {"room": "105", "day": "Wednesday", "start": "18:00", "end": "19:30", "title": "Kids Midweek Club", "leader": "Emily Watson", "cancelled": False},
    ],
}

MIGRATION_V1_SQL = """
CREATE TABLE IF NOT EXISTS age_groups (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    banner_bg TEXT NOT NULL,
    banner_text TEXT NOT NULL,
    badge_bg TEXT NOT NULL,
    badge_text TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    is_default INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    default_age_group_id TEXT REFERENCES age_groups(id) ON DELETE SET NULL,
    override_age_group_id TEXT REFERENCES age_groups(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    age_group_id TEXT NOT NULL REFERENCES age_groups(id) ON DELETE RESTRICT,
    room_id TEXT REFERENCES rooms(id) ON DELETE SET NULL,
    day TEXT NOT NULL,
    date TEXT,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    leader TEXT NOT NULL DEFAULT '',
    cancelled INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'upload',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'xlsx',
    summary_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by_ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username TEXT NOT NULL DEFAULT '',
    actor_ip TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    bucket_key TEXT PRIMARY KEY,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    window_start TEXT NOT NULL,
    locked_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_classes_room_day ON classes(room_id, day, start_time);
CREATE INDEX IF NOT EXISTS idx_classes_age_group ON classes(age_group_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
"""

MIGRATION_V2_SQL = """
CREATE TABLE IF NOT EXISTS staged_imports (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    source_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_staged_imports_expires ON staged_imports(expires_at);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection, max_retries: int = 8):
    attempt = 0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= max_retries:
                raise
            attempt += 1
            time.sleep(0.02 * attempt + random.uniform(0.0, 0.02))
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise


def get_state_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'state_version'").fetchone()
    return int(row["value"]) if row else 1


def bump_state_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO app_settings (key, value) VALUES ('state_version', '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
        """
    )
    return get_state_version(conn)


def _match_age_group_slug(conn: sqlite3.Connection, raw_value: str) -> str:
    candidate = (raw_value or "").strip().lower()
    rows = conn.execute(
        "SELECT id, label, aliases_json, is_default FROM age_groups ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    default_id = None
    for row in rows:
        if row["is_default"]:
            default_id = row["id"]
        if candidate == row["id"].lower() or candidate == row["label"].lower():
            return row["id"]
    for row in rows:
        try:
            aliases = json.loads(row["aliases_json"] or "[]")
        except json.JSONDecodeError:
            aliases = []
        if any(candidate == str(a).strip().lower() for a in aliases):
            return row["id"]
    return default_id or (rows[0]["id"] if rows else "adults")


def _seed_from_payload(conn: sqlite3.Connection, payload: dict, source: str = "upload"):
    now = utc_now_iso()
    rooms_map = payload.get("roomsMap") or {}
    schedule_list = payload.get("scheduleList") or []

    room_age_lookup = {}
    for room_id, room_info in rooms_map.items():
        rid = str(room_id).strip()
        if not rid or not isinstance(room_info, dict):
            continue
        name = str(room_info.get("name") or f"Room {rid}").strip()
        note = str(room_info.get("note") or "").strip()
        raw_age = str(room_info.get("ageGroup") or "Adults").strip()
        age_id = _match_age_group_slug(conn, raw_age)
        room_age_lookup[rid] = age_id
        conn.execute(
            """
            INSERT OR IGNORE INTO rooms
                (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
            VALUES (?, ?, ?, ?, NULL, 1, ?)
            """,
            (rid, name, note, age_id, now),
        )

    for item in schedule_list:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("room") or "").strip() or None
        raw_age = item.get("ageGroupId") or item.get("ageGroup")
        if raw_age:
            age_id = _match_age_group_slug(conn, str(raw_age))
        elif rid and rid in room_age_lookup:
            age_id = room_age_lookup[rid]
        else:
            age_id = _match_age_group_slug(conn, "Adults")

        if rid and rid not in room_age_lookup:
            conn.execute(
                """
                INSERT OR IGNORE INTO rooms
                    (id, name, note, default_age_group_id, override_age_group_id, version, updated_at)
                VALUES (?, ?, '', ?, NULL, 1, ?)
                """,
                (rid, f"Room {rid}", age_id, now),
            )
            room_age_lookup[rid] = age_id

        conn.execute(
            """
            INSERT INTO classes
                (age_group_id, room_id, day, date, start_time, end_time, title, topic, leader, cancelled, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                age_id,
                rid,
                str(item.get("day") or "Sunday").strip(),
                item.get("date") or None,
                str(item.get("start") or "09:00").strip(),
                str(item.get("end") or "10:00").strip(),
                str(item.get("title") or "Scheduled Class").strip(),
                str(item.get("topic") or "").strip(),
                str(item.get("leader") or "").strip(),
                1 if item.get("cancelled") else 0,
                source,
                now,
            ),
        )


def init_db(db_path: Path, legacy_json_path: Path | None = None, default_timezone: str = "America/Chicago"):
    conn = connect(db_path)
    migrated_backup_needed = False
    try:
        with write_transaction(conn):
            version = conn.execute("PRAGMA user_version;").fetchone()[0]
            if version < 1:
                for stmt in MIGRATION_V1_SQL.split(";"):
                    sql = stmt.strip()
                    if sql:
                        conn.execute(sql)
                conn.execute("PRAGMA user_version = 1;")
                version = 1
            if version < 2:
                for stmt in MIGRATION_V2_SQL.split(";"):
                    sql = stmt.strip()
                    if sql:
                        conn.execute(sql)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")

            now = utc_now_iso()
            existing_groups = conn.execute("SELECT COUNT(*) FROM age_groups").fetchone()[0]
            if existing_groups == 0:
                for ag in DEFAULT_AGE_GROUPS:
                    conn.execute(
                        """
                        INSERT INTO age_groups
                            (id, label, banner_bg, banner_text, badge_bg, badge_text, sort_order, aliases_json, is_default, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ag["id"],
                            ag["label"],
                            ag["banner_bg"],
                            ag["banner_text"],
                            ag["badge_bg"],
                            ag["badge_text"],
                            ag["sort_order"],
                            json.dumps(ag["aliases"]),
                            ag["is_default"],
                            now,
                        ),
                    )

            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('theme', 'dark')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('timezone', ?)",
                (default_timezone,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('state_version', '1')"
            )

            initialized = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'data_initialized'"
            ).fetchone()
            if not initialized:
                room_count = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
                class_count = conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
                if room_count == 0 and class_count == 0:
                    if legacy_json_path and Path(legacy_json_path).exists():
                        with Path(legacy_json_path).open("r", encoding="utf-8") as fh:
                            legacy_payload = json.load(fh)
                        _seed_from_payload(conn, legacy_payload, source="migrated_json")
                        migrated_backup_needed = True
                    else:
                        _seed_from_payload(conn, DEFAULT_SAMPLE_STATE, source="seed")
                conn.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('data_initialized', '1')"
                )
                bump_state_version(conn)
    finally:
        conn.close()

    if migrated_backup_needed and legacy_json_path and Path(legacy_json_path).exists():
        backup_path = Path(legacy_json_path).with_name(f"{Path(legacy_json_path).name}.migrated.bak")
        if not backup_path.exists():
            shutil.copy2(legacy_json_path, backup_path)
