from pathlib import Path

from flask import Flask, send_from_directory

from app.config import load_config
from app.db import close_db, init_db
from app.routes.auth import auth_bp
from app.routes.classes import classes_bp
from app.routes.rooms import rooms_bp
from app.routes.settings import settings_bp
from app.routes.state import state_bp


ROOT_DIR = Path(__file__).resolve().parent.parent


def create_app(test_config=None) -> Flask:
    app = Flask(
        __name__,
        static_folder=str(ROOT_DIR / "public"),
        static_url_path="",
    )
    app.config.update(load_config(test_config))

    init_db(
        db_path=app.config["DB_PATH"],
        legacy_json_path=app.config.get("LEGACY_JSON_PATH"),
        default_timezone=app.config["CHURCH_TIMEZONE"],
    )

    app.teardown_appcontext(close_db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(state_bp)
    app.register_blueprint(rooms_bp)
    app.register_blueprint(classes_bp)
    app.register_blueprint(settings_bp)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    return app
