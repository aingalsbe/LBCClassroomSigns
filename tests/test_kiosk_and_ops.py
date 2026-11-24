import json
from pathlib import Path

from app import create_app


def _login(client, username="admin", password="TestPassword123!"):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return {"X-CSRF-Token": res.get_json()["csrfToken"]}


def test_self_hosted_assets_strict_csp_and_manifest(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "m5.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_PASSWORD": "TestPassword123!",
            "SECRET_KEY": "m5-test-secret-key-at-least-32-chars!!",
        }
    )
    c = app.test_client()

    # 1. Static self-hosted assets all serve 200 OK
    css = c.get("/assets/styles.css")
    assert css.status_code == 200 and len(css.data) > 5000

    js = c.get("/assets/app.js")
    assert js.status_code == 200 and b"STATE_CACHE_KEY" in js.data

    logo = c.get("/assets/LBC-logo.png")
    assert logo.status_code == 200 and logo.data[:8] == b"\x89PNG\r\n\x1a\n"

    manifest_res = c.get("/manifest.webmanifest")
    assert manifest_res.status_code == 200
    manifest = json.loads(manifest_res.data.decode("utf-8"))
    assert manifest["display"] == "standalone"

    # 2. Strict script-src 'self' CSP and zero external runtime CDNs
    index_res = c.get("/")
    assert index_res.status_code == 200
    csp = index_res.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp
    assert "cdn.tailwindcss.com" not in csp
    assert "cdn.jsdelivr.net" not in csp
    assert "fonts.googleapis.com" not in csp

    html = Path("public/index.html").read_text(encoding="utf-8")
    assert "cdn.tailwindcss.com" not in html
    assert "cdn.jsdelivr.net" not in html
    assert "fonts.googleapis.com" not in html
    assert "<script>" not in html
    assert 'src="/assets/app.js"' in html
    assert 'href="/assets/styles.css"' in html
    assert 'apple-mobile-web-app-capable' in html
    # LBC logo belongs in the main app header, NOT inside the door kiosk display frame
    assert 'id="headerLogoPlate"' in html
    assert 'id="displayLogoPlate"' not in html


def test_show_logo_setting_toggle(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "m5_logo.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_PASSWORD": "TestPassword123!",
            "SECRET_KEY": "m5-test-secret-key-at-least-32-chars!!",
        }
    )
    c = app.test_client()
    assert c.get("/api/state").get_json()["settings"]["showLogo"] is True

    headers = _login(c)
    res = c.patch("/api/settings", json={"showLogo": False}, headers=headers)
    assert res.status_code == 200
    assert res.get_json()["settings"]["showLogo"] is False
    assert c.get("/api/state").get_json()["settings"]["showLogo"] is False


def test_container_hardening_and_env_example():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "appuser" in dockerfile
    assert "docker_entrypoint.py" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile

    entrypoint = Path("docker_entrypoint.py").read_text(encoding="utf-8")
    assert "os.setuid" in entrypoint and "os.setgid" in entrypoint

    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "change-me-now" not in compose
    assert "replace-with-long-random-string" not in compose

    env_ex = Path(".env.example").read_text(encoding="utf-8")
    assert "INITIAL_ADMIN_PASSWORD_HASH" in env_ex
    assert "SESSION_SECRET" in env_ex
    assert "CHURCH_TIMEZONE" in env_ex
