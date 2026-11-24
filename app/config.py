from datetime import timedelta
import os
from pathlib import Path


WEAK_SECRETS = {
    "",
    "dev-secret-change-in-production",
    "replace-with-long-random-string",
    "local-dev-secret",
    "change-me",
    "change-me-now",
    "secret",
}

WEAK_PASSWORDS = {
    "",
    "admin",
    "admin123",
    "password",
    "password123",
    "change-me",
    "change-me-now",
    "12345678",
}


def _parse_bool(val, default=False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def load_config(overrides=None):
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    is_prod = app_env == "production"

    data_file_env = os.environ.get("DATA_FILE")
    if data_file_env:
        legacy_json_path = Path(data_file_env)
        data_dir = legacy_json_path.parent
    else:
        data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
        legacy_json_path = data_dir / "schedule.json"

    db_path_env = os.environ.get("DB_PATH")
    db_path = Path(db_path_env) if db_path_env else (data_dir / "lbcsigns.db")

    secret_key = os.environ.get("SESSION_SECRET", "" if is_prod else "dev-secret-change-in-production")
    cookie_secure = _parse_bool(os.environ.get("COOKIE_SECURE"), default=is_prod)
    lifetime_hours = int(os.environ.get("SESSION_LIFETIME_HOURS", "8"))
    trusted_proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "1" if is_prod else "0"))
    trust_cloudflare = _parse_bool(os.environ.get("TRUST_CLOUDFLARE"), default=False)

    initial_admin_password = os.environ.get(
        "INITIAL_ADMIN_PASSWORD",
        os.environ.get("ADMIN_PASSWORD", "" if is_prod else "admin123"),
    )

    config = {
        "APP_ENV": app_env,
        "SECRET_KEY": secret_key,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": cookie_secure,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=lifetime_hours),
        "SESSION_REFRESH_EACH_REQUEST": True,
        "TRUSTED_PROXY_COUNT": trusted_proxy_count,
        "TRUST_CLOUDFLARE": trust_cloudflare,
        "INITIAL_ADMIN_USERNAME": os.environ.get("INITIAL_ADMIN_USERNAME", "admin").strip().lower(),
        "INITIAL_ADMIN_DISPLAY_NAME": os.environ.get("INITIAL_ADMIN_DISPLAY_NAME", "Primary Admin").strip(),
        "INITIAL_ADMIN_PASSWORD_HASH": os.environ.get("INITIAL_ADMIN_PASSWORD_HASH", "").strip(),
        "INITIAL_ADMIN_PASSWORD": initial_admin_password,
        "LOGIN_USER_MAX_ATTEMPTS": int(os.environ.get("LOGIN_USER_MAX_ATTEMPTS", "5")),
        "LOGIN_IP_MAX_ATTEMPTS": int(os.environ.get("LOGIN_IP_MAX_ATTEMPTS", "15")),
        "LOGIN_LOCKOUT_SECONDS": int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900")),
        "MIN_PASSWORD_LENGTH": int(os.environ.get("MIN_PASSWORD_LENGTH", "10")),
        "DATA_DIR": data_dir,
        "DB_PATH": db_path,
        "LEGACY_JSON_PATH": legacy_json_path,
        "CHURCH_TIMEZONE": os.environ.get("CHURCH_TIMEZONE", "America/Chicago"),
        "BUILD_ID": os.environ.get("BUILD_ID", "m2-security-hardening"),
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
    }

    if overrides:
        config.update(overrides)
        if "ADMIN_PASSWORD" in overrides and "INITIAL_ADMIN_PASSWORD" not in overrides:
            config["INITIAL_ADMIN_PASSWORD"] = overrides["ADMIN_PASSWORD"]
        if "DB_PATH" in overrides:
            config["DB_PATH"] = Path(overrides["DB_PATH"])
        if "LEGACY_JSON_PATH" in overrides and overrides["LEGACY_JSON_PATH"] is not None:
            config["LEGACY_JSON_PATH"] = Path(overrides["LEGACY_JSON_PATH"])
        if "APP_ENV" in overrides:
            config["APP_ENV"] = str(overrides["APP_ENV"]).strip().lower()
            if "SESSION_COOKIE_SECURE" not in overrides and "COOKIE_SECURE" not in overrides:
                config["SESSION_COOKIE_SECURE"] = config["APP_ENV"] == "production"
        if "COOKIE_SECURE" in overrides:
            config["SESSION_COOKIE_SECURE"] = _parse_bool(overrides["COOKIE_SECURE"])

    return config


def validate_security_config(config: dict):
    if config.get("APP_ENV") != "production":
        return

    secret = str(config.get("SECRET_KEY") or "").strip()
    if len(secret) < 32 or secret.lower() in WEAK_SECRETS:
        raise RuntimeError(
            "Refusing to start in production: SESSION_SECRET must be a strong random string of at least 32 characters."
        )
