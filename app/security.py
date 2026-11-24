from datetime import datetime, timedelta, timezone
import hmac
import secrets
import sqlite3

from flask import current_app, request, session

from app.db import write_transaction


CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "manifest-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def get_client_ip() -> str:
    if current_app.config.get("TRUST_CLOUDFLARE"):
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            return cf_ip[:64]
    return (request.remote_addr or "unknown")[:64]


def ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def rotate_csrf_token() -> str:
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def verify_csrf_token() -> bool:
    expected = session.get("csrf_token") or ""
    provided = request.headers.get("X-CSRF-Token") or ""
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(provided), str(expected))


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
    return response


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _buckets_for(ip_addr: str, username: str) -> list[tuple[str, int]]:
    ip_max = int(current_app.config.get("LOGIN_IP_MAX_ATTEMPTS", 15))
    user_max = int(current_app.config.get("LOGIN_USER_MAX_ATTEMPTS", 5))
    buckets = [(f"ip:{ip_addr}", ip_max)]
    uname = (username or "").strip().lower()
    if uname:
        buckets.append((f"user:{uname}", user_max))
    return buckets


def check_login_rate_limit(conn: sqlite3.Connection, ip_addr: str, username: str) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    max_retry = 0
    for bucket_key, _limit in _buckets_for(ip_addr, username):
        row = conn.execute(
            "SELECT locked_until FROM auth_rate_limits WHERE bucket_key = ?",
            (bucket_key,),
        ).fetchone()
        if not row or not row["locked_until"]:
            continue
        locked_until = _parse_iso(row["locked_until"])
        if locked_until and locked_until > now:
            retry_secs = max(1, int((locked_until - now).total_seconds()) + 1)
            if retry_secs > max_retry:
                max_retry = retry_secs
    if max_retry > 0:
        return False, max_retry
    return True, 0


def record_login_failure(conn: sqlite3.Connection, ip_addr: str, username: str) -> tuple[bool, int]:
    now = datetime.now(timezone.utc)
    window_secs = int(current_app.config.get("LOGIN_LOCKOUT_SECONDS", 900))
    now_iso = now.isoformat()
    locked_now = False
    retry_after = 0

    with write_transaction(conn):
        for bucket_key, limit in _buckets_for(ip_addr, username):
            row = conn.execute(
                "SELECT failed_attempts, window_start, locked_until FROM auth_rate_limits WHERE bucket_key = ?",
                (bucket_key,),
            ).fetchone()

            window_start = _parse_iso(row["window_start"]) if row else None
            expired = (window_start is None) or ((now - window_start).total_seconds() > window_secs)

            if not row or expired:
                attempts = 1
                w_start_iso = now_iso
            else:
                attempts = int(row["failed_attempts"]) + 1
                w_start_iso = row["window_start"]

            locked_until_iso = None
            if attempts >= limit:
                locked_until_dt = now + timedelta(seconds=window_secs)
                locked_until_iso = locked_until_dt.isoformat()
                locked_now = True
                retry_after = max(retry_after, window_secs)

            conn.execute(
                """
                INSERT INTO auth_rate_limits (bucket_key, failed_attempts, window_start, locked_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bucket_key) DO UPDATE SET
                    failed_attempts = excluded.failed_attempts,
                    window_start = excluded.window_start,
                    locked_until = excluded.locked_until
                """,
                (bucket_key, attempts, w_start_iso, locked_until_iso),
            )

    return locked_now, retry_after


def clear_user_rate_limit(conn: sqlite3.Connection, username: str):
    uname = (username or "").strip().lower()
    if not uname:
        return
    with write_transaction(conn):
        conn.execute("DELETE FROM auth_rate_limits WHERE bucket_key = ?", (f"user:{uname}",))
