from io import BytesIO

from flask import Blueprint, current_app, g, jsonify, request, send_file

from app.db import get_db
from app.routes.auth import require_admin
from app.security import get_client_ip
from app.services.imports import (
    commit_staged_import,
    list_import_snapshots,
    rollback_to_snapshot,
    save_staged_import,
)
from app.services.schedule import build_state_payload
from app.services.spreadsheets import (
    SpreadsheetFormatError,
    build_export_xlsx,
    build_template_xlsx,
    parse_and_validate_upload,
)


imports_bp = Blueprint("imports", __name__)


@imports_bp.route("/api/import/preview", methods=["POST"])
@require_admin
def preview_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded (expected multipart form field 'file')."}), 400
    uploaded = request.files["file"]
    filename = uploaded.filename or ""
    if not filename.strip():
        return jsonify({"error": "Please select an .xlsx or .csv file."}), 400

    raw_bytes = uploaded.read()
    db = get_db()
    try:
        preview = parse_and_validate_upload(
            db,
            filename=filename,
            raw_bytes=raw_bytes,
            church_tz=current_app.config["CHURCH_TIMEZONE"],
        )
    except SpreadsheetFormatError as exc:
        return jsonify({"error": str(exc)}), 400

    preview_token = None
    if preview["canCommit"]:
        preview_token = save_staged_import(db, user_id=g.current_user["id"], preview=preview)

    return jsonify(
        {
            "ok": True,
            "previewToken": preview_token,
            "filename": preview["filename"],
            "sourceType": preview["sourceType"],
            "canCommit": preview["canCommit"],
            "summary": preview["summary"],
            "diff": preview["diff"],
            "errors": preview["errors"],
            "warnings": preview["warnings"],
            "previewRows": preview["classes"][:250],
            "truncated": len(preview["classes"]) > 250,
        }
    )


@imports_bp.route("/api/import/commit", methods=["POST"])
@require_admin
def commit_import():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    token = str(body.get("previewToken") or "").strip()
    if not token:
        return jsonify({"error": "previewToken is required"}), 400

    mode = str(body.get("mode") or "replace").strip().lower()
    preserve_notices = bool(body.get("preserveRoomNotices", True))
    preserve_overrides = bool(body.get("preserveRoomOverrides", True))
    preserve_admin_classes = bool(body.get("preserveAdminClasses", True))

    db = get_db()
    try:
        result = commit_staged_import(
            db,
            token=token,
            user_id=g.current_user["id"],
            actor_ip=get_client_ip(),
            mode=mode,
            preserve_room_notices=preserve_notices,
            preserve_room_overrides=preserve_overrides,
            preserve_admin_classes=preserve_admin_classes,
        )
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    state = build_state_payload(
        db,
        build_id=current_app.config["BUILD_ID"],
        default_tz=current_app.config["CHURCH_TIMEZONE"],
    )
    return jsonify({"ok": True, **result, "state": state})


@imports_bp.route("/api/import/history", methods=["GET"])
@require_admin
def import_history():
    db = get_db()
    limit = request.args.get("limit", default=10, type=int)
    return jsonify({"snapshots": list_import_snapshots(db, limit=limit)})


@imports_bp.route("/api/import/rollback/<int:snapshot_id>", methods=["POST"])
@require_admin
def import_rollback(snapshot_id: int):
    db = get_db()
    try:
        result = rollback_to_snapshot(
            db,
            snapshot_id=snapshot_id,
            user_id=g.current_user["id"],
            actor_ip=get_client_ip(),
        )
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    state = build_state_payload(
        db,
        build_id=current_app.config["BUILD_ID"],
        default_tz=current_app.config["CHURCH_TIMEZONE"],
    )
    return jsonify({"ok": True, **result, "state": state})


@imports_bp.route("/api/template.xlsx", methods=["GET"])
@require_admin
def download_template():
    db = get_db()
    content = build_template_xlsx(db)
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="classroom_schedule_template.xlsx",
    )


@imports_bp.route("/api/export.xlsx", methods=["GET"])
@require_admin
def export_schedule():
    db = get_db()
    content = build_export_xlsx(db)
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="updated_classroom_schedule.xlsx",
    )
