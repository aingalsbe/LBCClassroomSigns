from pathlib import Path

from flask import Flask, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import load_config, validate_security_config
from app.db import close_db, connect, init_db
from app.routes.audit import audit_bp
from app.routes.auth import auth_bp
from app.routes.classes import classes_bp
from app.routes.rooms import rooms_bp
from app.routes.settings import settings_bp
from app.routes.state import state_bp
from app.routes.users import users_bp
from app.security import apply_security_headers
from app.services.users import bootstrap_initial_admin


ROOT_DIR = Path(__file__).resolve().parent.parent


def create_app(test_config=None) -> Flask:
    config = load_config(test_config)
    validate_security_config(config)

    app = Flask(
        __name__,
        static_folder=str(ROOT_DIR / "public"),
        static_url_path="",
    )
    app.config.update(config)

    proxy_hops = int(app.config.get("TRUSTED_PROXY_COUNT", 0))
    if proxy_hops > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_prefix=proxy_hops,
        )

    init_db(
        db_path=app.config["DB_PATH"],
        legacy_json_path=app.config.get("LEGACY_JSON_PATH"),
        default_timezone=app.config["CHURCH_TIMEZONE"],
    )

    conn = connect(app.config["DB_PATH"])
    try:
        bootstrap_initial_admin(conn, app.config)
    finally:
        conn.close()

    app.teardown_appcontext(close_db)
    app.after_request(apply_security_headers)

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(state_bp)
    app.register_blueprint(rooms_bp)
    app.register_blueprint(classes_bp)
    app.register_blueprint(settings_bp)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    return app
