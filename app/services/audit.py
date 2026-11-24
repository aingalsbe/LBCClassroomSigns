import json
import sqlite3

from flask import g, has_request_context

from app.db import utc_now_iso
from app.security import get_client_ip


def log_audit(
    conn: sqlite3.Connection,
    action: str,
    target_type: str = "",
    target_id: str = "",
    details: dict | None = None,
    user_id: int | None = None,
    username: str | None = None,
    actor_ip: str | None = None,
):
    if has_request_context():
        current_user = getattr(g, "current_user", None)
        if user_id is None and current_user:
            user_id = current_user["id"]
        if username is None and current_user:
            username = current_user["username"]
        if actor_ip is None:
            actor_ip = get_client_ip()

    conn.execute(
        """
        INSERT INTO audit_log
            (timestamp, user_id, username, actor_ip, action, target_type, target_id, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now_iso(),
            user_id,
            str(username or "")[:80],
            str(actor_ip or "")[:64],
            str(action)[:80],
            str(target_type or "")[:60],
            str(target_id or "")[:120],
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )


def list_audit_entries(
    conn: sqlite3.Connection,
    limit: int = 50,
    action: str | None = None,
    username: str | None = None,
) -> list[dict]:
    limit = max(1, min(int(limit or 50), 250))
    clauses = []
    params = []
    if action:
        clauses.append("action = ?")
        params.append(action.strip())
    if username:
        clauses.append("username = ?")
        params.append(username.strip().lower())

    sql = "SELECT * FROM audit_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    entries = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except json.JSONDecodeError:
            details = {}
        entries.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "userId": row["user_id"],
                "username": row["username"],
                "actorIp": row["actor_ip"],
                "action": row["action"],
                "targetType": row["target_type"],
                "targetId": row["target_id"],
                "details": details,
            }
        )
    return entries
