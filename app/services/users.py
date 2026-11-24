import logging
import re
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from app.config import WEAK_PASSWORDS
from app.db import utc_now_iso, write_transaction


logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
VALID_ROLES = ("super_admin", "admin")

# Precomputed hash used for constant-time verification when a username is not found
DUMMY_PASSWORD_HASH = generate_password_hash("dummy-nonexistent-user-secret-padding")


def normalize_username(raw: str) -> str | None:
    candidate = str(raw or "").strip().lower()
    if not USERNAME_RE.match(candidate):
        return None
    return candidate


def validate_password_strength(password: str, min_length: int = 10) -> str | None:
    pwd = str(password or "")
    if len(pwd) < min_length:
        return f"Password must be at least {min_length} characters long."
    if pwd.strip().lower() in WEAK_PASSWORDS:
        return "Password is too common. Please choose a stronger password."
    return None


def hash_password(password: str) -> str:
    return generate_password_hash(str(password))


def serialize_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"],
        "role": row["role"],
        "isActive": bool(row["is_active"]),
        "mustChangePassword": bool(row["must_change_password"]),
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"],
    }


def authenticate_user(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    uname = str(username or "").strip().lower()
    pwd = str(password or "")
    row = conn.execute("SELECT * FROM users WHERE username = ?", (uname,)).fetchone()
    if not row:
        # Run a dummy check to equalize timing and prevent username enumeration
        check_password_hash(DUMMY_PASSWORD_HASH, pwd)
        return None
    if not row["is_active"]:
        check_password_hash(DUMMY_PASSWORD_HASH, pwd)
        return None
    if not check_password_hash(row["password_hash"], pwd):
        return None
    return row


def bootstrap_initial_admin(conn: sqlite3.Connection, config: dict):
    with write_transaction(conn):
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return

        username = normalize_username(config.get("INITIAL_ADMIN_USERNAME", "admin")) or "admin"
        display_name = str(config.get("INITIAL_ADMIN_DISPLAY_NAME") or "Primary Admin").strip()[:80]
        pwd_hash = str(config.get("INITIAL_ADMIN_PASSWORD_HASH") or "").strip()
        raw_pwd = str(config.get("INITIAL_ADMIN_PASSWORD") or "")
        is_prod = config.get("APP_ENV") == "production"

        if pwd_hash:
            final_hash = pwd_hash
        else:
            if is_prod:
                if len(raw_pwd) < int(config.get("MIN_PASSWORD_LENGTH", 10)) or raw_pwd.strip().lower() in WEAK_PASSWORDS:
                    raise RuntimeError(
                        "Refusing to bootstrap initial admin in production: configure "
                        "INITIAL_ADMIN_PASSWORD_HASH (recommended) or a strong INITIAL_ADMIN_PASSWORD."
                    )
                logger.warning(
                    "Bootstrapping initial admin from plaintext INITIAL_ADMIN_PASSWORD. "
                    "Generate a hash via `python -m app.cli hash-password` and use INITIAL_ADMIN_PASSWORD_HASH."
                )
            elif not raw_pwd:
                raw_pwd = "admin123"
            final_hash = hash_password(raw_pwd)

        conn.execute(
            """
            INSERT INTO users
                (username, display_name, password_hash, role, is_active, must_change_password, created_at)
            VALUES (?, ?, ?, 'super_admin', 1, 0, ?)
            """,
            (username, display_name, final_hash, utc_now_iso()),
        )
