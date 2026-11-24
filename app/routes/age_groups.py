import json
import sqlite3

from flask import Blueprint, jsonify, request

from app.db import bump_state_version, get_db, utc_now_iso, write_transaction
from app.routes.auth import require_admin
from app.services.age_groups import (
    CURATED_PALETTE,
    contrast_ratio,
    derive_badge_colors,
    list_age_groups_with_usage,
    normalize_aliases,
    normalize_hex,
    pick_accessible_text_color,
    slugify_age_group_id,
)
from app.services.audit import log_audit
from app.services.schedule import serialize_age_group


age_groups_bp = Blueprint("age_groups", __name__)


@age_groups_bp.route("/api/age-groups", methods=["GET"])
def list_age_groups():
    db = get_db()
    return jsonify(
        {
            "ageGroups": list_age_groups_with_usage(db),
            "palette": CURATED_PALETTE,
        }
    )


@age_groups_bp.route("/api/age-groups", methods=["POST"])
@require_admin
def create_age_group():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    label = str(body.get("label") or "").strip()[:60]
    if not label:
        return jsonify({"error": "Age group label is required"}), 400

    banner_bg = normalize_hex(body.get("bannerBg"))
    if not banner_bg:
        return jsonify({"error": "bannerBg must be a valid #RRGGBB hex color"}), 400

    if body.get("bannerText"):
        banner_text = normalize_hex(body.get("bannerText"))
        if not banner_text:
            return jsonify({"error": "bannerText must be a valid #RRGGBB hex color"}), 400
    else:
        banner_text = pick_accessible_text_color(banner_bg)

    default_badge_bg, default_badge_text = derive_badge_colors(banner_bg)
    badge_bg = str(body.get("badgeBg") or "").strip()[:60] or default_badge_bg
    if body.get("badgeText"):
        badge_text = normalize_hex(body.get("badgeText"))
        if not badge_text:
            return jsonify({"error": "badgeText must be a valid #RRGGBB hex color"}), 400
    else:
        badge_text = default_badge_text

    aliases = normalize_aliases(body.get("aliases"))
    if label.lower() not in [a.lower() for a in aliases]:
        aliases.insert(0, label)

    is_default = 1 if body.get("isDefault") else 0
    raw_id = body.get("id")
    slug = slugify_age_group_id(str(raw_id) if raw_id else label)

    db = get_db()
    now = utc_now_iso()
    try:
        with write_transaction(db):
            # Ensure unique slug if generated from label
            base_slug = slug
            suffix = 2
            while db.execute("SELECT 1 FROM age_groups WHERE id = ?", (slug,)).fetchone():
                if raw_id:
                    return jsonify({"error": f"Age group id '{slug}' already exists"}), 409
                slug = f"{base_slug}-{suffix}"
                suffix += 1

            max_order = db.execute("SELECT COALESCE(MAX(sort_order), 0) FROM age_groups").fetchone()[0]
            if is_default:
                db.execute("UPDATE age_groups SET is_default = 0")

            db.execute(
                """
                INSERT INTO age_groups
                    (id, label, banner_bg, banner_text, badge_bg, badge_text, sort_order, aliases_json, is_default, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    label,
                    banner_bg,
                    banner_text,
                    badge_bg,
                    badge_text,
                    max_order + 1,
                    json.dumps(aliases, ensure_ascii=False),
                    is_default,
                    now,
                ),
            )
            state_version = bump_state_version(db)
            log_audit(
                db,
                action="age_group_created",
                target_type="age_group",
                target_id=slug,
                details={"label": label, "bannerBg": banner_bg, "bannerText": banner_text},
            )
            row = db.execute("SELECT * FROM age_groups WHERE id = ?", (slug,)).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"An age group named '{label}' already exists"}), 409

    serialized = serialize_age_group(row)
    serialized["contrastRatio"] = contrast_ratio(serialized["bannerBg"], serialized["bannerText"])
    return jsonify({"ok": True, "ageGroup": serialized, "stateVersion": state_version}), 201


@age_groups_bp.route("/api/age-groups/<group_id>", methods=["PATCH"])
@require_admin
def update_age_group(group_id: str):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    db = get_db()
    try:
        with write_transaction(db):
            existing = db.execute("SELECT * FROM age_groups WHERE id = ?", (group_id,)).fetchone()
            if not existing:
                return jsonify({"error": f"Age group '{group_id}' not found"}), 404

            label = existing["label"]
            if "label" in body:
                label = str(body["label"] or "").strip()[:60]
                if not label:
                    return jsonify({"error": "Age group label cannot be empty"}), 400

            banner_bg = existing["banner_bg"]
            bg_changed = False
            if "bannerBg" in body:
                banner_bg = normalize_hex(body["bannerBg"])
                if not banner_bg:
                    return jsonify({"error": "bannerBg must be a valid #RRGGBB hex color"}), 400
                bg_changed = banner_bg != existing["banner_bg"]

            banner_text = existing["banner_text"]
            if body.get("autoText") or (bg_changed and "bannerText" not in body):
                banner_text = pick_accessible_text_color(banner_bg)
            elif "bannerText" in body:
                banner_text = normalize_hex(body["bannerText"])
                if not banner_text:
                    return jsonify({"error": "bannerText must be a valid #RRGGBB hex color"}), 400

            badge_bg = existing["badge_bg"]
            badge_text = existing["badge_text"]
            if bg_changed and "badgeBg" not in body and "badgeText" not in body:
                badge_bg, badge_text = derive_badge_colors(banner_bg)
            else:
                if "badgeBg" in body and body["badgeBg"]:
                    badge_bg = str(body["badgeBg"]).strip()[:60]
                if "badgeText" in body and body["badgeText"]:
                    bt = normalize_hex(body["badgeText"])
                    if not bt:
                        return jsonify({"error": "badgeText must be a valid #RRGGBB hex color"}), 400
                    badge_text = bt

            aliases_json = existing["aliases_json"]
            if "aliases" in body:
                aliases_json = json.dumps(normalize_aliases(body["aliases"]), ensure_ascii=False)

            is_default = existing["is_default"]
            if "isDefault" in body:
                if body["isDefault"]:
                    db.execute("UPDATE age_groups SET is_default = 0")
                    is_default = 1
                else:
                    is_default = 0

            now = utc_now_iso()
            db.execute(
                """
                UPDATE age_groups
                SET label = ?, banner_bg = ?, banner_text = ?, badge_bg = ?, badge_text = ?,
                    aliases_json = ?, is_default = ?, updated_at = ?
                WHERE id = ?
                """,
                (label, banner_bg, banner_text, badge_bg, badge_text, aliases_json, is_default, now, group_id),
            )
            state_version = bump_state_version(db)
            log_audit(
                db,
                action="age_group_updated",
                target_type="age_group",
                target_id=group_id,
                details={"label": label, "bannerBg": banner_bg, "bannerText": banner_text},
            )
            updated = db.execute("SELECT * FROM age_groups WHERE id = ?", (group_id,)).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"An age group named '{label}' already exists"}), 409

    serialized = serialize_age_group(updated)
    serialized["contrastRatio"] = contrast_ratio(serialized["bannerBg"], serialized["bannerText"])
    return jsonify({"ok": True, "ageGroup": serialized, "stateVersion": state_version})


@age_groups_bp.route("/api/age-groups/reorder", methods=["PUT"])
@require_admin
def reorder_age_groups():
    body = request.get_json(silent=True) or {}
    order = body.get("order")
    if not isinstance(order, list) or not order:
        return jsonify({"error": "order must be a non-empty list of age group IDs"}), 400

    db = get_db()
    with write_transaction(db):
        existing_ids = {r["id"] for r in db.execute("SELECT id FROM age_groups").fetchall()}
        cleaned = [str(g).strip() for g in order if str(g).strip() in existing_ids]
        if len(cleaned) != len(existing_ids):
            return jsonify({"error": "order must include every existing age group ID exactly once"}), 400

        now = utc_now_iso()
        for idx, gid in enumerate(cleaned, start=1):
            db.execute(
                "UPDATE age_groups SET sort_order = ?, updated_at = ? WHERE id = ?",
                (idx, now, gid),
            )
        state_version = bump_state_version(db)
        log_audit(
            db,
            action="age_groups_reordered",
            target_type="age_group",
            target_id="all",
            details={"order": cleaned},
        )

    return jsonify(
        {
            "ok": True,
            "ageGroups": list_age_groups_with_usage(db),
            "stateVersion": state_version,
        }
    )


@age_groups_bp.route("/api/age-groups/<group_id>", methods=["DELETE"])
@require_admin
def delete_age_group(group_id: str):
    body = request.get_json(silent=True) or {}
    reassign_to = str(body.get("reassignTo") or request.args.get("reassignTo") or "").strip() or None

    db = get_db()
    with write_transaction(db):
        existing = db.execute("SELECT * FROM age_groups WHERE id = ?", (group_id,)).fetchone()
        if not existing:
            return jsonify({"error": f"Age group '{group_id}' not found"}), 404

        total_groups = db.execute("SELECT COUNT(*) FROM age_groups").fetchone()[0]
        if total_groups <= 1:
            return jsonify({"error": "Cannot delete the last remaining age group"}), 400

        class_count = db.execute(
            "SELECT COUNT(*) FROM classes WHERE age_group_id = ?", (group_id,)
        ).fetchone()[0]
        room_count = db.execute(
            "SELECT COUNT(*) FROM rooms WHERE default_age_group_id = ? OR override_age_group_id = ?",
            (group_id, group_id),
        ).fetchone()[0]

        if class_count > 0 and not reassign_to:
            return (
                jsonify(
                    {
                        "error": f"Age group '{existing['label']}' is assigned to {class_count} class(es). Choose a replacement age group to reassign them.",
                        "code": "REASSIGN_REQUIRED",
                        "classCount": class_count,
                        "roomCount": room_count,
                    }
                ),
                409,
            )

        if reassign_to:
            if reassign_to == group_id:
                return jsonify({"error": "Replacement age group must differ from the deleted group"}), 400
            target_row = db.execute("SELECT id, label FROM age_groups WHERE id = ?", (reassign_to,)).fetchone()
            if not target_row:
                return jsonify({"error": f"Replacement age group '{reassign_to}' not found"}), 400

            now = utc_now_iso()
            db.execute(
                "UPDATE classes SET age_group_id = ?, version = version + 1, updated_at = ? WHERE age_group_id = ?",
                (reassign_to, now, group_id),
            )
            db.execute(
                "UPDATE rooms SET default_age_group_id = ?, version = version + 1, updated_at = ? WHERE default_age_group_id = ?",
                (reassign_to, now, group_id),
            )
            db.execute(
                "UPDATE rooms SET override_age_group_id = ?, version = version + 1, updated_at = ? WHERE override_age_group_id = ?",
                (reassign_to, now, group_id),
            )

        was_default = bool(existing["is_default"])
        db.execute("DELETE FROM age_groups WHERE id = ?", (group_id,))

        if was_default:
            fallback_id = reassign_to
            if not fallback_id:
                row = db.execute("SELECT id FROM age_groups ORDER BY sort_order ASC LIMIT 1").fetchone()
                fallback_id = row["id"] if row else None
            if fallback_id:
                db.execute("UPDATE age_groups SET is_default = 1 WHERE id = ?", (fallback_id,))

        state_version = bump_state_version(db)
        log_audit(
            db,
            action="age_group_deleted",
            target_type="age_group",
            target_id=group_id,
            details={
                "label": existing["label"],
                "reassignedTo": reassign_to,
                "reassignedClasses": class_count,
                "reassignedRooms": room_count,
            },
        )

    return jsonify(
        {
            "ok": True,
            "deletedGroupId": group_id,
            "reassignedTo": reassign_to,
            "reassignedClasses": class_count,
            "stateVersion": state_version,
        }
    )
