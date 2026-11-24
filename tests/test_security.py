from pathlib import Path
import subprocess
import sys

import pytest

from app import create_app
from app.services.users import hash_password


def _login(client, username, password, extra_headers=None):
    return client.post(
        "/api/login",
        json={"username": username, "password": password},
        headers=extra_headers or {},
    )


def test_production_fail_fast_weak_secrets(tmp_path):
    # Short / placeholder SESSION_SECRET must refuse to boot in production
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(
            {
                "APP_ENV": "production",
                "SECRET_KEY": "dev-secret-change-in-production",
                "INITIAL_ADMIN_PASSWORD": "StrongProdPassword123!",
                "DB_PATH": tmp_path / "p1.db",
            }
        )

    # Weak initial admin password must refuse to boot in production
    with pytest.raises(RuntimeError, match="INITIAL_ADMIN_PASSWORD"):
        create_app(
            {
                "APP_ENV": "production",
                "SECRET_KEY": "a" * 48,
                "INITIAL_ADMIN_PASSWORD": "admin123",
                "DB_PATH": tmp_path / "p2.db",
            }
        )

    # Strong secret + pre-hashed password boots cleanly with secure cookies enabled
    strong_hash = hash_password("VeryStrongChurchPass!2026")
    prod_app = create_app(
        {
            "APP_ENV": "production",
            "SECRET_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef",
            "INITIAL_ADMIN_USERNAME": "aaron",
            "INITIAL_ADMIN_PASSWORD_HASH": strong_hash,
            "DB_PATH": tmp_path / "p3.db",
        }
    )
    assert prod_app.config["SESSION_COOKIE_SECURE"] is True


def test_multi_admin_rbac_and_immediate_revocation(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "rbac.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_USERNAME": "aaron",
            "INITIAL_ADMIN_DISPLAY_NAME": "Aaron Ingalsbe",
            "INITIAL_ADMIN_PASSWORD": "AaronSuperPassword1!",
            "SECRET_KEY": "s" * 40,
        }
    )

    super_client = app.test_client()
    r = _login(super_client, "aaron", "AaronSuperPassword1!")
    assert r.status_code == 200
    super_data = r.get_json()
    assert super_data["user"]["role"] == "super_admin"
    assert "password_hash" not in super_data["user"]
    super_headers = {"X-CSRF-Token": super_data["csrfToken"]}

    # Super admin creates staff admin account 'justin'
    created = super_client.post(
        "/api/users",
        json={
            "username": "justin",
            "displayName": "Justin Pedigo",
            "password": "JustinTempPassword1!",
            "role": "admin",
            "mustChangePassword": True,
        },
        headers=super_headers,
    )
    assert created.status_code == 201
    justin_id = created.get_json()["user"]["id"]

    # Justin logs in
    staff_client = app.test_client()
    jr = _login(staff_client, "justin", "JustinTempPassword1!")
    assert jr.status_code == 200
    jdata = jr.get_json()
    assert jdata["user"]["mustChangePassword"] is True
    staff_headers = {"X-CSRF-Token": jdata["csrfToken"]}

    # Staff admin can edit rooms, but CANNOT list/create users (403)
    assert staff_client.patch("/api/rooms/101", json={"note": "Justin notice"}, headers=staff_headers).status_code == 200
    assert staff_client.get("/api/users").status_code == 403
    assert (
        staff_client.post(
            "/api/users",
            json={"username": "evil", "displayName": "Evil", "password": "EvilPassword123!"},
            headers=staff_headers,
        ).status_code
        == 403
    )

    # Staff admin changes own password -> rotates CSRF token & clears mustChangePassword
    cp = staff_client.post(
        "/api/auth/change-password",
        json={"currentPassword": "JustinTempPassword1!", "newPassword": "JustinNewPassword2026!"},
        headers=staff_headers,
    )
    assert cp.status_code == 200
    assert cp.get_json()["user"]["mustChangePassword"] is False
    new_staff_csrf = cp.get_json()["csrfToken"]
    assert new_staff_csrf != staff_headers["X-CSRF-Token"]

    # Old CSRF token must now be rejected (403)
    assert staff_client.patch("/api/rooms/101", json={"note": "Old token"}, headers=staff_headers).status_code == 403
    staff_headers = {"X-CSRF-Token": new_staff_csrf}
    assert staff_client.patch("/api/rooms/101", json={"note": "New token"}, headers=staff_headers).status_code == 200

    # Super admin deactivates Justin -> Justin's active session is revoked immediately!
    deact = super_client.patch(f"/api/users/{justin_id}", json={"isActive": False}, headers=super_headers)
    assert deact.status_code == 200
    assert staff_client.patch("/api/rooms/101", json={"note": "Revoked"}, headers=staff_headers).status_code == 401

    # Last super_admin cannot deactivate or demote themselves
    aaron_id = super_data["user"]["id"]
    assert super_client.patch(f"/api/users/{aaron_id}", json={"isActive": False}, headers=super_headers).status_code == 400
    assert super_client.patch(f"/api/users/{aaron_id}", json={"role": "admin"}, headers=super_headers).status_code == 400


def test_csrf_enforcement(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "csrf.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_PASSWORD": "StrongPassword123!",
            "SECRET_KEY": "c" * 40,
        }
    )
    c = app.test_client()
    r = _login(c, "admin", "StrongPassword123!")
    token = r.get_json()["csrfToken"]

    # Missing CSRF token -> 403
    missing = c.post("/api/rooms", json={"id": "500", "name": "Room 500"})
    assert missing.status_code == 403
    assert missing.get_json()["code"] == "CSRF_ERROR"

    # Wrong CSRF token -> 403
    wrong = c.post("/api/rooms", json={"id": "500", "name": "Room 500"}, headers={"X-CSRF-Token": "forged"})
    assert wrong.status_code == 403

    # Logout without CSRF -> 403
    assert c.post("/api/logout").status_code == 403

    # Valid CSRF -> 201 / 200
    assert c.post("/api/rooms", json={"id": "500", "name": "Room 500"}, headers={"X-CSRF-Token": token}).status_code == 201
    assert c.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200


def test_rate_limiting_and_proxy_fix(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "rl.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_USERNAME": "admin",
            "INITIAL_ADMIN_PASSWORD": "StrongPassword123!",
            "SECRET_KEY": "r" * 40,
            "TRUSTED_PROXY_COUNT": 1,
            "LOGIN_USER_MAX_ATTEMPTS": 3,
            "LOGIN_IP_MAX_ATTEMPTS": 5,
            "LOGIN_LOCKOUT_SECONDS": 300,
        }
    )
    c = app.test_client()

    # 3 failed attempts for 'admin' from IP 203.0.113.10 trigger 429 + Retry-After
    assert _login(c, "admin", "wrong1", {"X-Forwarded-For": "203.0.113.10"}).status_code == 401
    assert _login(c, "admin", "wrong2", {"X-Forwarded-For": "203.0.113.10"}).status_code == 401
    r3 = _login(c, "admin", "wrong3", {"X-Forwarded-For": "203.0.113.10"})
    assert r3.status_code == 429
    assert int(r3.headers["Retry-After"]) > 0

    # Even correct password for 'admin' is blocked while locked
    r4 = _login(c, "admin", "StrongPassword123!", {"X-Forwarded-For": "203.0.113.10"})
    assert r4.status_code == 429

    # Spraying random usernames from a second IP hits the IP limit at 5 attempts
    ip2 = {"X-Forwarded-For": "203.0.113.20"}
    for i in range(4):
        assert _login(c, f"ghost{i}", "wrong", ip2).status_code == 401
    r_ip_lock = _login(c, "ghost4", "wrong", ip2)
    assert r_ip_lock.status_code == 429


def test_security_headers_and_zero_innerhtml(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "hdr.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "SECRET_KEY": "h" * 40,
        }
    )
    c = app.test_client()
    for path in ("/", "/api/state"):
        res = c.get(path)
        assert res.status_code == 200
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["X-Frame-Options"] == "DENY"
        assert "Referrer-Policy" in res.headers
        assert "Content-Security-Policy" in res.headers

    html = Path("public/index.html").read_text(encoding="utf-8")
    script_part = html.split("<script>", 1)[1]
    assert ".innerHTML" not in script_part


def test_audit_log_attribution_and_cli(tmp_path):
    db_path = tmp_path / "audit.db"
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_USERNAME": "aaron",
            "INITIAL_ADMIN_PASSWORD": "AaronPassword123!",
            "SECRET_KEY": "u" * 40,
            "TRUSTED_PROXY_COUNT": 1,
        }
    )
    c = app.test_client()
    _login(c, "aaron", "wrong-pass", {"X-Forwarded-For": "198.51.100.42"})
    lr = _login(c, "aaron", "AaronPassword123!", {"X-Forwarded-For": "198.51.100.42"})
    headers = {"X-CSRF-Token": lr.get_json()["csrfToken"], "X-Forwarded-For": "198.51.100.42"}

    assert c.patch("/api/rooms/101", json={"note": "Audit test"}, headers=headers).status_code == 200

    audit_res = c.get("/api/audit?limit=10")
    assert audit_res.status_code == 200
    entries = audit_res.get_json()["entries"]
    actions = [e["action"] for e in entries]
    assert "room_updated" in actions
    assert "login_success" in actions
    assert "login_failed" in actions
    room_ev = next(e for e in entries if e["action"] == "room_updated")
    assert room_ev["username"] == "aaron"
    assert room_ev["actorIp"] == "198.51.100.42"
    assert room_ev["targetId"] == "101"

    # CLI hash-password & create-admin smoke check
    project_root = str(Path(__file__).resolve().parent.parent)
    out = subprocess.check_output(
        [sys.executable, "-m", "app.cli", "hash-password", "--password", "CliStrongPass123!"],
        cwd=project_root,
        text=True,
    ).strip()
    assert out.startswith(("scrypt:", "pbkdf2:"))
