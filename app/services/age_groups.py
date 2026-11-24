import json
import re
import sqlite3

from app.services.schedule import serialize_age_group


HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# All presets verified >= 4.5:1 WCAG AA contrast with #ffffff text
CURATED_PALETTE = [
    {"name": "Indigo", "hex": "#4338ca"},
    {"name": "Teal", "hex": "#0f766e"},
    {"name": "Sky Blue", "hex": "#0369a1"},
    {"name": "Emerald", "hex": "#047857"},
    {"name": "Amber", "hex": "#b45309"},
    {"name": "Purple", "hex": "#7e22ce"},
    {"name": "Raspberry", "hex": "#be185d"},
    {"name": "Royal Blue", "hex": "#1d4ed8"},
    {"name": "Crimson", "hex": "#b91c1c"},
    {"name": "Slate", "hex": "#475569"},
]


def normalize_hex(val: str) -> str | None:
    text = str(val or "").strip()
    if not HEX_COLOR_RE.match(text):
        return None
    return text.lower()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    r, g, b = _hex_to_rgb(hex_color)
    channels = []
    for c in (r, g, b):
        srgb = c / 255.0
        channels.append(srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(hex1: str, hex2: str) -> float:
    l1 = relative_luminance(hex1)
    l2 = relative_luminance(hex2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def pick_accessible_text_color(bg_hex: str) -> str:
    white_ratio = contrast_ratio(bg_hex, "#ffffff")
    dark_ratio = contrast_ratio(bg_hex, "#0f172a")
    return "#ffffff" if white_ratio >= dark_ratio else "#0f172a"


def derive_badge_colors(banner_bg: str) -> tuple[str, str]:
    r, g, b = _hex_to_rgb(banner_bg)
    badge_bg = f"rgba({r}, {g}, {b}, 0.22)"
    # Lighten slightly toward white for readable badge text on dark slate tables
    tr = min(255, int(r + (255 - r) * 0.58))
    tg = min(255, int(g + (255 - g) * 0.58))
    tb = min(255, int(b + (255 - b) * 0.58))
    badge_text = f"#{tr:02x}{tg:02x}{tb:02x}"
    return badge_bg, badge_text


def slugify_age_group_id(label: str) -> str:
    slug = SLUG_NON_ALNUM_RE.sub("-", str(label or "").strip().lower()).strip("-")
    return slug[:40] or "group"


def normalize_aliases(raw_aliases) -> list[str]:
    if raw_aliases is None:
        return []
    if isinstance(raw_aliases, str):
        items = [part.strip() for part in raw_aliases.split(",")]
    elif isinstance(raw_aliases, list):
        items = [str(part or "").strip() for part in raw_aliases]
    else:
        return []
    seen = set()
    cleaned = []
    for item in items:
        item = item[:60]
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned[:25]


def list_age_groups_with_usage(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM age_groups ORDER BY sort_order ASC, label ASC"
    ).fetchall()

    class_counts = {
        r["age_group_id"]: r["cnt"]
        for r in conn.execute("SELECT age_group_id, COUNT(*) AS cnt FROM classes GROUP BY age_group_id").fetchall()
    }
    room_counts = {
        r["ag_id"]: r["cnt"]
        for r in conn.execute(
            """
            SELECT ag_id, COUNT(DISTINCT id) AS cnt
            FROM (
                SELECT id, default_age_group_id AS ag_id FROM rooms WHERE default_age_group_id IS NOT NULL
                UNION ALL
                SELECT id, override_age_group_id AS ag_id FROM rooms WHERE override_age_group_id IS NOT NULL
            )
            GROUP BY ag_id
            """
        ).fetchall()
    }

    result = []
    for row in rows:
        item = serialize_age_group(row)
        item["classCount"] = class_counts.get(row["id"], 0)
        item["roomCount"] = room_counts.get(row["id"], 0)
        item["contrastRatio"] = contrast_ratio(item["bannerBg"], item["bannerText"])
        result.append(item)
    return result
