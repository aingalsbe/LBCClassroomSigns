import os
from pathlib import Path


def load_config(overrides=None):
    data_file_env = os.environ.get("DATA_FILE")
    if data_file_env:
        legacy_json_path = Path(data_file_env)
        data_dir = legacy_json_path.parent
    else:
        data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
        legacy_json_path = data_dir / "schedule.json"

    db_path_env = os.environ.get("DB_PATH")
    db_path = Path(db_path_env) if db_path_env else (data_dir / "lbcsigns.db")

    config = {
        "SECRET_KEY": os.environ.get("SESSION_SECRET", "dev-secret-change-in-production"),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD", "admin123"),
        "DATA_DIR": data_dir,
        "DB_PATH": db_path,
        "LEGACY_JSON_PATH": legacy_json_path,
        "CHURCH_TIMEZONE": os.environ.get("CHURCH_TIMEZONE", "America/Chicago"),
        "BUILD_ID": os.environ.get("BUILD_ID", "m1-sqlite-wal"),
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
    }
    if overrides:
        config.update(overrides)
        if "DB_PATH" in overrides:
            config["DB_PATH"] = Path(overrides["DB_PATH"])
        if "LEGACY_JSON_PATH" in overrides and overrides["LEGACY_JSON_PATH"] is not None:
            config["LEGACY_JSON_PATH"] = Path(overrides["LEGACY_JSON_PATH"])
    return config
