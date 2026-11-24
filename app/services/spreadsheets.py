import csv
from datetime import date, datetime, time
from io import BytesIO, StringIO
import re
import sqlite3
import zipfile
from zoneinfo import ZoneInfo

import openpyxl
from openpyxl.styles import Font, PatternFill

from app.services.schedule import (
    VALID_DAYS,
    natural_sort_key,
    normalize_date,
    normalize_day,
    normalize_time,
    resolve_age_group_id,
)


MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_UNCOMPRESSED_ZIP_BYTES = 25 * 1024 * 1024  # 25 MB zip-bomb cap
MAX_ZIP_ENTRIES = 100
MAX_ROWS = 5000
MAX_COLS = 50

ALLOWED_EXTENSIONS = {".xlsx", ".csv"}
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

HEADER_ALIASES = {
    "room": "room",
    "roomid": "room",
    "roomnumber": "room",
    "roomname": "room_name",
    "agegroup": "age_group",
    "age": "age_group",
    "agefamily": "age_group",
    "day": "day",
    "dayofweek": "day",
    "starttime": "start",
    "start": "start",
    "endtime": "end",
    "end": "end",
    "classtitle": "title",
    "title": "title",
    "class": "title",
    "classname": "title",
    "leader": "leader",
    "leaders": "leader",
    "teacher": "leader",
    "teachers": "leader",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "date": "date",
    "topic": "topic",
    "note": "note",
    "doornotice": "note",
}

REQUIRED_FIELDS = ("room", "age_group", "day", "start", "end", "title")
FIELD_LABELS = {
    "room": "Room",
    "room_name": "RoomName",
    "age_group": "AgeGroup",
    "day": "Day",
    "start": "StartTime",
    "end": "EndTime",
    "title": "ClassTitle",
    "leader": "Leader",
    "cancelled": "Cancelled",
    "date": "Date",
    "topic": "Topic",
    "note": "Note",
}


class SpreadsheetFormatError(ValueError):
    """Raised when the uploaded file fails format, size, or security intake checks."""


def clean_text(val, max_len: int) -> str:
    if val is None:
        return ""
    text = CONTROL_CHARS_RE.sub("", str(val)).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def sanitize_excel_cell(val):
    if isinstance(val, str) and val and val[0] in FORMULA_PREFIXES:
        return "'" + val
    return val


def _canonical_header(raw) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", str(raw or "").strip().lower())
    return HEADER_ALIASES.get(cleaned, "")


def _parse_cell_time(val) -> str | None:
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    if isinstance(val, datetime):
        return f"{val.hour:02d}:{val.minute:02d}"
    if isinstance(val, time):
        return f"{val.hour:02d}:{val.minute:02d}"
    return normalize_time(val)


def _parse_cell_date(val) -> str | None:
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    return normalize_date(str(val))


def _parse_cancelled(val) -> tuple[bool, bool]:
    if val is None:
        return False, True
    if isinstance(val, bool):
        return val, True
    text = str(val).strip().lower()
    if text == "":
        return False, True
    if text in ("yes", "y", "true", "1", "cancelled", "canceled"):
        return True, True
    if text in ("no", "n", "false", "0"):
        return False, True
    return False, False


def _read_xlsx_rows(raw_bytes: bytes) -> list[tuple[int, list]]:
    if len(raw_bytes) < 4 or raw_bytes[:4] != b"PK\x03\x04":
        raise SpreadsheetFormatError("File does not appear to be a valid .xlsx workbook (invalid file signature).")

    try:
        with zipfile.ZipFile(BytesIO(raw_bytes), "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise SpreadsheetFormatError("Workbook archive contains too many entries.")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > MAX_UNCOMPRESSED_ZIP_BYTES:
                raise SpreadsheetFormatError("Workbook uncompressed size exceeds the 25 MB safety limit.")
            names = {info.filename for info in infos}
            if "xl/workbook.xml" not in names:
                raise SpreadsheetFormatError("Uploaded ZIP file is not a valid .xlsx spreadsheet.")
    except zipfile.BadZipFile as exc:
        raise SpreadsheetFormatError("Uploaded .xlsx file is corrupted or unreadable.") from exc

    wb = None
    try:
        wb = openpyxl.load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
        if not wb.sheetnames:
            raise SpreadsheetFormatError("Workbook contains no worksheets.")
        ws = wb[wb.sheetnames[0]]
        rows = []
        for idx, row_vals in enumerate(ws.iter_rows(values_only=True), start=1):
            if idx > MAX_ROWS + 1:
                raise SpreadsheetFormatError(f"Spreadsheet exceeds maximum allowed limit of {MAX_ROWS} rows.")
            vals = list(row_vals or [])
            if len(vals) > MAX_COLS:
                raise SpreadsheetFormatError(f"Spreadsheet exceeds maximum allowed limit of {MAX_COLS} columns.")
            rows.append((idx, vals))
        return rows
    except SpreadsheetFormatError:
        raise
    except Exception as exc:
        raise SpreadsheetFormatError(f"Could not parse .xlsx workbook: {exc}") from exc
    finally:
        if wb is not None:
            wb.close()


def _read_csv_rows(raw_bytes: bytes) -> list[tuple[int, list]]:
    if b"\x00" in raw_bytes:
        raise SpreadsheetFormatError("CSV file contains binary null bytes and was rejected.")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SpreadsheetFormatError("CSV file must be valid UTF-8 text.") from exc

    reader = csv.reader(StringIO(text))
    rows = []
    for idx, vals in enumerate(reader, start=1):
        if idx > MAX_ROWS + 1:
            raise SpreadsheetFormatError(f"CSV exceeds maximum allowed limit of {MAX_ROWS} rows.")
        if len(vals) > MAX_COLS:
            raise SpreadsheetFormatError(f"CSV exceeds maximum allowed limit of {MAX_COLS} columns.")
        rows.append((idx, vals))
    return rows


def detect_extension(filename: str) -> str:
    name = (filename or "").strip().lower()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[1]


def parse_and_validate_upload(
    conn: sqlite3.Connection,
    filename: str,
    raw_bytes: bytes,
    church_tz: str = "America/Chicago",
) -> dict:
    ext = detect_extension(filename)
    if ext == ".xls":
        raise SpreadsheetFormatError(
            "Legacy .xls (Excel 97-2003) files are not supported. Please save as .xlsx or .csv."
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise SpreadsheetFormatError("Unsupported file type. Please upload an .xlsx or .csv file.")
    if not raw_bytes:
        raise SpreadsheetFormatError("Uploaded file is empty.")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise SpreadsheetFormatError("Uploaded file exceeds the 5 MB size limit.")

    source_type = ext.lstrip(".")
    raw_rows = _read_xlsx_rows(raw_bytes) if ext == ".xlsx" else _read_csv_rows(raw_bytes)
    if not raw_rows:
        raise SpreadsheetFormatError("Spreadsheet is empty.")

    # Locate the first non-empty row as header
    header_idx = None
    header_vals = None
    for row_num, vals in raw_rows:
        if any(str(v or "").strip() for v in vals):
            header_idx = row_num
            header_vals = vals
            break
    if header_vals is None:
        raise SpreadsheetFormatError("Spreadsheet contains no header row.")

    col_map = {}
    for col_i, h_val in enumerate(header_vals):
        canonical = _canonical_header(h_val)
        if canonical and canonical not in col_map:
            col_map[canonical] = col_i

    errors = []
    warnings = []

    missing_required = [FIELD_LABELS[f] for f in REQUIRED_FIELDS if f not in col_map]
    if missing_required:
        errors.append(
            {
                "row": header_idx,
                "column": "Headers",
                "message": f"Missing required columns: {', '.join(missing_required)}",
            }
        )
        return _build_preview_result(
            conn=conn,
            filename=filename,
            source_type=source_type,
            rooms={},
            classes=[],
            errors=errors,
            warnings=warnings,
        )

    age_rows = conn.execute("SELECT id, label, banner_bg, badge_bg, badge_text FROM age_groups ORDER BY sort_order ASC").fetchall()
    valid_age_labels = [r["label"] for r in age_rows]
    age_info_map = {r["id"]: dict(r) for r in age_rows}

    today_str = datetime.now(ZoneInfo(church_tz)).strftime("%Y-%m-%d")

    parsed_rooms = {}
    room_name_conflicts_reported = set()
    parsed_classes = []

    def cell(vals, field_name):
        idx = col_map.get(field_name)
        if idx is None or idx >= len(vals):
            return None
        return vals[idx]

    for row_num, vals in raw_rows:
        if row_num <= header_idx:
            continue
        if not any(v is not None and str(v).strip() != "" for v in vals):
            continue

        row_has_error = False

        raw_room = clean_text(cell(vals, "room"), 40)
        if not raw_room:
            errors.append({"row": row_num, "column": "Room", "message": "Room is required."})
            row_has_error = True
        elif "/" in raw_room:
            errors.append({"row": row_num, "column": "Room", "message": "Room ID cannot contain '/'."})
            row_has_error = True

        raw_room_name = clean_text(cell(vals, "room_name"), 120)
        room_name = raw_room_name or (f"Room {raw_room}" if raw_room else "")

        raw_age = clean_text(cell(vals, "age_group"), 80)
        age_group_id = resolve_age_group_id(conn, raw_age) if raw_age else None
        if not raw_age:
            errors.append({"row": row_num, "column": "AgeGroup", "message": "AgeGroup is required."})
            row_has_error = True
        elif not age_group_id:
            errors.append(
                {
                    "row": row_num,
                    "column": "AgeGroup",
                    "message": f"Unknown AgeGroup '{raw_age}'. Valid groups: {', '.join(valid_age_labels)}.",
                }
            )
            row_has_error = True

        raw_day = clean_text(cell(vals, "day"), 30)
        day = normalize_day(raw_day) if raw_day else None
        if not raw_day:
            errors.append({"row": row_num, "column": "Day", "message": "Day is required."})
            row_has_error = True
        elif not day:
            errors.append(
                {"row": row_num, "column": "Day", "message": f"Invalid day '{raw_day}' (use Sunday–Saturday)."}
            )
            row_has_error = True

        raw_start = cell(vals, "start")
        start_time = _parse_cell_time(raw_start)
        if not start_time:
            errors.append(
                {"row": row_num, "column": "StartTime", "message": f"Unreadable StartTime '{raw_start or ''}'."}
            )
            row_has_error = True

        raw_end = cell(vals, "end")
        end_time = _parse_cell_time(raw_end)
        if not end_time:
            errors.append(
                {"row": row_num, "column": "EndTime", "message": f"Unreadable EndTime '{raw_end or ''}'."}
            )
            row_has_error = True

        if start_time and end_time and start_time >= end_time:
            errors.append(
                {
                    "row": row_num,
                    "column": "EndTime",
                    "message": f"EndTime ({end_time}) must be later than StartTime ({start_time}).",
                }
            )
            row_has_error = True

        title = clean_text(cell(vals, "title"), 140)
        if not title:
            errors.append({"row": row_num, "column": "ClassTitle", "message": "ClassTitle is required."})
            row_has_error = True

        topic = clean_text(cell(vals, "topic"), 160)
        leader = clean_text(cell(vals, "leader"), 200)
        note = clean_text(cell(vals, "note"), 240)

        cancelled, cancelled_ok = _parse_cancelled(cell(vals, "cancelled"))
        if not cancelled_ok:
            errors.append(
                {
                    "row": row_num,
                    "column": "Cancelled",
                    "message": f"Invalid Cancelled value '{cell(vals, 'cancelled')}' (use YES or NO).",
                }
            )
            row_has_error = True

        raw_date_cell = cell(vals, "date")
        date_val = None
        if raw_date_cell is not None and str(raw_date_cell).strip() != "":
            date_val = _parse_cell_date(raw_date_cell)
            if not date_val:
                errors.append(
                    {
                        "row": row_num,
                        "column": "Date",
                        "message": f"Invalid Date '{raw_date_cell}' (expected YYYY-MM-DD).",
                    }
                )
                row_has_error = True
            elif date_val < today_str:
                warnings.append(
                    {
                        "row": row_num,
                        "column": "Date",
                        "message": f"Date override {date_val} is in the past.",
                    }
                )

        if row_has_error:
            continue

        if not leader:
            warnings.append(
                {"row": row_num, "column": "Leader", "message": f"Missing leader for '{title}' in Room {raw_room}."}
            )

        if raw_room not in parsed_rooms:
            parsed_rooms[raw_room] = {
                "id": raw_room,
                "name": room_name,
                "defaultAgeGroupId": age_group_id,
                "note": note,
            }
        else:
            existing_name = parsed_rooms[raw_room]["name"]
            if raw_room_name and raw_room_name != existing_name and (raw_room, raw_room_name) not in room_name_conflicts_reported:
                room_name_conflicts_reported.add((raw_room, raw_room_name))
                warnings.append(
                    {
                        "row": row_num,
                        "column": "RoomName",
                        "message": f"Room '{raw_room}' has conflicting RoomName '{raw_room_name}' (keeping '{existing_name}').",
                    }
                )
            if note and not parsed_rooms[raw_room]["note"]:
                parsed_rooms[raw_room]["note"] = note

        ag_meta = age_info_map[age_group_id]
        parsed_classes.append(
            {
                "rowNum": row_num,
                "roomId": raw_room,
                "roomName": parsed_rooms[raw_room]["name"],
                "ageGroupId": age_group_id,
                "ageGroupLabel": ag_meta["label"],
                "badgeBg": ag_meta["badge_bg"],
                "badgeText": ag_meta["badge_text"],
                "day": day,
                "date": date_val,
                "start": start_time,
                "end": end_time,
                "title": title,
                "topic": topic,
                "leader": leader,
                "cancelled": cancelled,
            }
        )

    if not parsed_classes and not errors:
        errors.append({"row": header_idx, "column": "Schedule", "message": "No data rows found below header row."})

    # Detect overlapping active classes within the uploaded spreadsheet
    by_slot = {}
    for cls in parsed_classes:
        if cls["cancelled"]:
            continue
        key = (cls["roomId"], cls["day"], cls["date"])
        by_slot.setdefault(key, []).append(cls)

    for (rid, day_name, _dt), slot_classes in by_slot.items():
        slot_classes.sort(key=lambda c: (c["start"], c["end"]))
        for i in range(len(slot_classes) - 1):
            a = slot_classes[i]
            b = slot_classes[i + 1]
            if a["end"] > b["start"]:
                warnings.append(
                    {
                        "row": b["rowNum"],
                        "column": "StartTime",
                        "message": (
                            f"Overlapping active classes in Room {rid} on {day_name}: "
                            f"'{a['title']}' ({a['start']}–{a['end']}) overlaps '{b['title']}' ({b['start']}–{b['end']})."
                        ),
                    }
                )

    return _build_preview_result(
        conn=conn,
        filename=filename,
        source_type=source_type,
        rooms=parsed_rooms,
        classes=parsed_classes,
        errors=errors,
        warnings=warnings,
    )


def _build_preview_result(
    conn: sqlite3.Connection,
    filename: str,
    source_type: str,
    rooms: dict,
    classes: list,
    errors: list,
    warnings: list,
) -> dict:
    existing_rooms = {
        r["id"]: dict(r)
        for r in conn.execute("SELECT id, name, note, override_age_group_id FROM rooms").fetchall()
    }
    existing_classes_count = conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
    admin_classes = conn.execute(
        "SELECT id, room_id, title, day, start_time FROM classes WHERE source = 'admin'"
    ).fetchall()

    incoming_ids = set(rooms.keys())
    existing_ids = set(existing_rooms.keys())

    rooms_added = sorted(incoming_ids - existing_ids, key=natural_sort_key)
    rooms_updated = sorted(incoming_ids & existing_ids, key=natural_sort_key)
    rooms_removed = sorted(existing_ids - incoming_ids, key=natural_sort_key)

    notices_count = sum(1 for r in existing_rooms.values() if (r["note"] or "").strip())
    overrides_count = sum(1 for r in existing_rooms.values() if r["override_age_group_id"])
    orphaned = []
    for rid in rooms_removed:
        r = existing_rooms[rid]
        if (r["note"] or "").strip() or r["override_age_group_id"]:
            orphaned.append({"roomId": rid, "name": r["name"], "reason": "Room not in uploaded file"})
            warnings.append(
                {
                    "row": 0,
                    "column": "Room",
                    "message": f"Existing room '{rid}' has a live Door Notice or Color Override, but is not in this file (will be removed in Replace mode).",
                }
            )

    for ac in admin_classes:
        if ac["room_id"] and ac["room_id"] not in incoming_ids:
            orphaned.append(
                {
                    "roomId": ac["room_id"],
                    "name": ac["title"],
                    "reason": f"Admin-edited class '{ac['title']}' is in Room '{ac['room_id']}' which is not in this file",
                }
            )

    summary = {
        "roomsTotal": len(rooms),
        "classesTotal": len(classes),
        "errorsCount": len(errors),
        "warningsCount": len(warnings),
        "existingRoomsCount": len(existing_rooms),
        "existingClassesCount": existing_classes_count,
    }
    diff = {
        "roomsAdded": rooms_added,
        "roomsUpdated": rooms_updated,
        "roomsRemoved": rooms_removed,
        "existingNoticesCount": notices_count,
        "existingColorOverridesCount": overrides_count,
        "existingAdminClassesCount": len(admin_classes),
        "orphanedOverrides": orphaned,
    }

    return {
        "filename": clean_text(filename, 180) or "upload",
        "sourceType": source_type,
        "canCommit": len(errors) == 0 and len(classes) > 0,
        "summary": summary,
        "diff": diff,
        "errors": errors,
        "warnings": warnings,
        "rooms": rooms,
        "classes": classes,
    }


def _style_header_row(ws):
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font


def _autofit_columns(ws):
    for col in ws.columns:
        max_len = 10
        col_letter = col[0].column_letter
        for cell in col:
            val_len = len(str(cell.value or ""))
            if val_len > max_len:
                max_len = min(val_len, 45)
        ws.column_dimensions[col_letter].width = max_len + 3


def build_template_xlsx(conn: sqlite3.Connection) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"

    headers = [
        "Room",
        "RoomName",
        "AgeGroup",
        "Day",
        "StartTime",
        "EndTime",
        "ClassTitle",
        "Leader",
        "Cancelled",
        "Topic",
        "Note",
    ]
    ws.append(headers)
    _style_header_row(ws)

    sample_rows = [
        ["WC 101", "WC 101 (Cafe)", "Seniors", "Sunday", "08:00", "09:15", "Senior Adults 7", "Dan Griffin", "NO", "", ""],
        ["WC 101", "WC 101 (Cafe)", "Adults", "Sunday", "09:30", "10:45", "Empty Nesters 5", "Greg Guthrie & Arlan Suderman", "NO", "", ""],
        ["WC 101", "WC 101 (Cafe)", "Seniors", "Sunday", "11:00", "12:15", "Senior Adults 4", "Carlos Brewer & Scott Willis", "NO", "", ""],
        ["Foundry", "The Foundry", "Teen", "Sunday", "09:30", "10:45", "Middle School Life Groups", "Brian Griffin", "NO", "", ""],
        ["250", "Room 250", "College", "Sunday", "09:30", "10:45", "College Life", "Kevin and Wendy Bost", "NO", "", ""],
        ["Kids", "Kids Wing (6 wks - 5th Grade)", "Kids", "Sunday", "09:30", "10:45", "Kids Life Groups", "Sam Sievers", "NO", "", "Check in at Welcome Desk"],
    ]
    for row in sample_rows:
        ws.append([sanitize_excel_cell(v) for v in row])
    _autofit_columns(ws)

    # Dynamic Reference tab listing all live DB Age Groups and accepted import aliases
    ref = wb.create_sheet("Age Groups Reference")
    ref.append(["AgeGroup (Use This Name)", "Accepted Import Aliases", "Banner Hex Color"])
    _style_header_row(ref)
    for ag in conn.execute("SELECT label, aliases_json, banner_bg FROM age_groups ORDER BY sort_order ASC, label ASC").fetchall():
        import json

        try:
            aliases = ", ".join(json.loads(ag["aliases_json"] or "[]"))
        except Exception:
            aliases = ""
        ref.append([sanitize_excel_cell(ag["label"]), sanitize_excel_cell(aliases), sanitize_excel_cell(ag["banner_bg"])])
    _autofit_columns(ref)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_export_xlsx(conn: sqlite3.Connection) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Schedule"

    headers = [
        "Room",
        "RoomName",
        "AgeGroup",
        "Day",
        "StartTime",
        "EndTime",
        "ClassTitle",
        "Leader",
        "Cancelled",
        "Topic",
        "Date",
    ]
    ws.append(headers)
    _style_header_row(ws)

    rooms = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM rooms").fetchall()}
    age_groups = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM age_groups").fetchall()}
    day_order = {d: i for i, d in enumerate(VALID_DAYS)}

    classes = conn.execute("SELECT * FROM classes").fetchall()
    sorted_classes = sorted(
        classes,
        key=lambda c: (
            natural_sort_key(c["room_id"] or "zzzz"),
            day_order.get(c["day"], 99),
            c["start_time"],
            c["id"],
        ),
    )

    for c in sorted_classes:
        room = rooms.get(c["room_id"])
        ag = age_groups.get(c["age_group_id"])
        row = [
            c["room_id"] or "",
            room["name"] if room else "Unassigned",
            ag["label"] if ag else c["age_group_id"],
            c["day"],
            c["start_time"],
            c["end_time"],
            c["title"],
            c["leader"] or "",
            "YES" if c["cancelled"] else "NO",
            c["topic"] or "",
            c["date"] or "",
        ]
        ws.append([sanitize_excel_cell(v) for v in row])
    _autofit_columns(ws)

    # Rooms tab (includes empty rooms, door notices, and manual color overrides)
    rws = wb.create_sheet("Rooms")
    rws.append(["Room", "RoomName", "DefaultAgeGroup", "ColorOverride", "DoorNotice"])
    _style_header_row(rws)
    for rid in sorted(rooms.keys(), key=natural_sort_key):
        r = rooms[rid]
        def_ag = age_groups.get(r["default_age_group_id"])
        ovr_ag = age_groups.get(r["override_age_group_id"])
        rws.append(
            [
                sanitize_excel_cell(r["id"]),
                sanitize_excel_cell(r["name"]),
                sanitize_excel_cell(def_ag["label"] if def_ag else ""),
                sanitize_excel_cell(ovr_ag["label"] if ovr_ag else ""),
                sanitize_excel_cell(r["note"] or ""),
            ]
        )
    _autofit_columns(rws)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
