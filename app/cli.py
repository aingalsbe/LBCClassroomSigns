import argparse
import getpass
import sys

from app.config import load_config
from app.db import connect, init_db, utc_now_iso, write_transaction
from app.services.audit import log_audit
from app.services.users import (
    VALID_ROLES,
    hash_password,
    normalize_username,
    validate_password_strength,
)


def _prompt_password(explicit: str | None, min_length: int) -> str:
    pwd = explicit if explicit is not None else getpass.getpass("Password: ")
    if explicit is None:
        confirm = getpass.getpass("Confirm password: ")
        if pwd != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
    err = validate_password_strength(pwd, min_length=min_length)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    return pwd


def cmd_hash_password(args, config):
    pwd = _prompt_password(args.password, int(config["MIN_PASSWORD_LENGTH"]))
    print(hash_password(pwd))


def cmd_create_admin(args, config):
    username = normalize_username(args.username)
    if not username:
        print("Error: invalid username (3-40 lowercase chars, digits, '.', '_', '-').", file=sys.stderr)
        sys.exit(1)
    role = args.role.strip().lower()
    if role not in VALID_ROLES:
        print("Error: role must be 'admin' or 'super_admin'.", file=sys.stderr)
        sys.exit(1)
    pwd = _prompt_password(args.password, int(config["MIN_PASSWORD_LENGTH"]))
    init_db(config["DB_PATH"], config.get("LEGACY_JSON_PATH"), config["CHURCH_TIMEZONE"])
    conn = connect(config["DB_PATH"])
    try:
        with write_transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, role, is_active, must_change_password, created_at)
                VALUES (?, ?, ?, ?, 1, 0, ?)
                """,
                (username, args.display_name.strip()[:80], hash_password(pwd), role, utc_now_iso()),
            )
            log_audit(
                conn,
                action="user_created_cli",
                target_type="user",
                target_id=str(cur.lastrowid),
                details={"username": username, "role": role},
                username="cli",
                actor_ip="local",
            )
        print(f"Created {role} user '{username}'.")
    finally:
        conn.close()


def cmd_reset_password(args, config):
    username = normalize_username(args.username)
    if not username:
        print("Error: invalid username.", file=sys.stderr)
        sys.exit(1)
    pwd = _prompt_password(args.password, int(config["MIN_PASSWORD_LENGTH"]))
    init_db(config["DB_PATH"], config.get("LEGACY_JSON_PATH"), config["CHURCH_TIMEZONE"])
    conn = connect(config["DB_PATH"])
    try:
        with write_transaction(conn):
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if not row:
                print(f"Error: user '{username}' not found.", file=sys.stderr)
                sys.exit(1)
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (hash_password(pwd), row["id"]),
            )
            conn.execute("DELETE FROM auth_rate_limits WHERE bucket_key = ?", (f"user:{username}",))
            log_audit(
                conn,
                action="password_reset_cli",
                target_type="user",
                target_id=str(row["id"]),
                details={"username": username},
                username="cli",
                actor_ip="local",
            )
        print(f"Reset password for '{username}'.")
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="LBC Classroom Signs admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hash = sub.add_parser("hash-password", help="Generate a Werkzeug password hash for INITIAL_ADMIN_PASSWORD_HASH")
    p_hash.add_argument("--password", help="Password to hash (omit to prompt securely)")

    p_create = sub.add_parser("create-admin", help="Create a new admin or super_admin user in SQLite")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--display-name", required=True)
    p_create.add_argument("--role", default="super_admin", choices=["admin", "super_admin"])
    p_create.add_argument("--password", help="Password (omit to prompt securely)")

    p_reset = sub.add_parser("reset-password", help="Reset a user's password and clear lockouts")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", help="New password (omit to prompt securely)")

    args = parser.parse_args(argv)
    config = load_config()
    if args.command == "hash-password":
        cmd_hash_password(args, config)
    elif args.command == "create-admin":
        cmd_create_admin(args, config)
    elif args.command == "reset-password":
        cmd_reset_password(args, config)


if __name__ == "__main__":
    main()
