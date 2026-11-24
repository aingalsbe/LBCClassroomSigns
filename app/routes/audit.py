from flask import Blueprint, jsonify, request

from app.db import get_db
from app.routes.auth import require_admin
from app.services.audit import list_audit_entries


audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/api/audit", methods=["GET"])
@require_admin
def get_audit_log():
    db = get_db()
    limit = request.args.get("limit", default=50, type=int)
    action = request.args.get("action")
    username = request.args.get("username")
    entries = list_audit_entries(db, limit=limit, action=action, username=username)
    return jsonify({"entries": entries})
