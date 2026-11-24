from io import BytesIO
from pathlib import Path
import zipfile

import openpyxl
import pytest

from app import create_app


def _make_xlsx(rows, sheet_name="Schedule") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _login(client, username="admin", password="TestPassword123!"):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return {"X-CSRF-Token": res.get_json()["csrfToken"]}


@pytest.fixture
def app_instance(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "m3.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_USERNAME": "admin",
            "INITIAL_ADMIN_PASSWORD": "TestPassword123!",
            "SECRET_KEY": "m3-test-secret-key-at-least-32-chars!!",
        }
    )


@pytest.fixture
def admin_session(app_instance):
    c = app_instance.test_client()
    return c, _login(c)


def test_xlsx_preview_commit_and_rollback_lbc_schedule(admin_session):
    c, headers = admin_session

    # Verify initial seeded state (Room 101 exists)
    initial = c.get("/api/state").get_json()
    assert "101" in initial["roomsMap"]
    initial_room_count = len(initial["rooms"])
    initial_class_count = len(initial["scheduleList"])

    rows = [
        ["Room", "RoomName", "AgeGroup", "Day", "StartTime", "EndTime", "ClassTitle", "Leader", "Cancelled"],
        ["WC 101", "WC 101 (Cafe)", "Seniors", "Sunday", "08:00", "09:15", "Senior Adults 7", "Dan Griffin", "NO"],
        ["WC 101", "WC 101 (Cafe)", "Adults", "Sunday", "09:30", "10:45", "Empty Nesters 5", "Greg Guthrie & Arlan Suderman", "NO"],
        ["WC 101", "WC 101 (Cafe)", "Seniors", "Sunday", "11:00", "12:15", "Senior Adults 4", "Ed Coulter, Jeff Greener, Randy Schmidt & Charles McLain", "YES"],
        ["232-234", "Room 232-234", "Seniors", "Sunday", "08:00", "09:15", "Senior Adults 8", "Derrick Cook & Boyd Peters", "NO"],
        ["232-234", "Room 232-234", "Adults", "Sunday", "09:30", "10:45", "Adults Open", "Steve Barnes", "NO"],
        ["Foundry", "The Foundry", "Teen", "Sunday", "09:30", "10:45", "Middle School Life Groups", "Brian Griffin", "NO"],
    ]
    xlsx_bytes = _make_xlsx(rows)

    prev_res = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(xlsx_bytes), "ClassroomScheduleUpload.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert prev_res.status_code == 200
    prev = prev_res.get_json()
    assert prev["canCommit"] is True
    assert prev["previewToken"]
    assert prev["summary"]["roomsTotal"] == 3
    assert prev["summary"]["classesTotal"] == 6
    assert prev["errors"] == []

    # Commit replace
    commit_res = c.post(
        "/api/import/commit",
        json={"previewToken": prev["previewToken"], "mode": "replace"},
        headers=headers,
    )
    assert commit_res.status_code == 200
    commit_data = commit_res.get_json()
    snap_id = commit_data["snapshotId"]

    wc101 = [cl for cl in commit_data["state"]["scheduleList"] if cl["room"] == "WC 101"]
    assert [(cl["start"], cl["ageGroupId"], cl["cancelled"]) for cl in wc101] == [
        ("08:00", "seniors", False),
        ("09:30", "adults", False),
        ("11:00", "seniors", True),
    ]

    # Preview token is one-time use (cannot be re-committed)
    reuse = c.post(
        "/api/import/commit",
        json={"previewToken": prev["previewToken"], "mode": "replace"},
        headers=headers,
    )
    assert reuse.status_code == 404

    # History shows the snapshot, and rollback restores the pre-import state
    hist = c.get("/api/import/history").get_json()["snapshots"]
    assert any(s["id"] == snap_id and s["createdBy"] == "admin" for s in hist)

    rb = c.post(f"/api/import/rollback/{snap_id}", headers=headers)
    assert rb.status_code == 200
    rb_data = rb.get_json()
    assert rb_data["restoredRooms"] == initial_room_count
    assert rb_data["restoredClasses"] == initial_class_count
    assert "101" in rb_data["state"]["roomsMap"]


def test_csv_utf8_bom_and_binary_rejection(admin_session):
    c, headers = admin_session
    csv_text = (
        "\ufeffRoom,RoomName,AgeGroup,Day,StartTime,EndTime,ClassTitle,Leader,Cancelled\n"
        "240,Room 240,Adults,Sun,9:30 AM,10:45 AM,Women of Faith,Barbara Porter,NO\n"
    ).encode("utf-8")

    res = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(csv_text), "sunday.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["canCommit"] is True
    assert data["previewRows"][0]["day"] == "Sunday"
    assert data["previewRows"][0]["start"] == "09:30"
    assert data["previewRows"][0]["end"] == "10:45"

    # Binary null bytes in CSV must be rejected
    bad_csv = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(b"Room,Day\x00,Title\n"), "bad.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert bad_csv.status_code == 400


def test_security_guards_xls_magic_bytes_zip_bomb_and_token_isolation(app_instance, admin_session):
    c, headers = admin_session

    # 1. Legacy .xls rejected
    r_xls = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(b"dummy"), "legacy.xls")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_xls.status_code == 400
    assert ".xls" in r_xls.get_json()["error"]

    # 2. Fake .xlsx magic bytes rejected
    r_fake = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(b"not a zip file at all"), "fake.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_fake.status_code == 400

    # 3. ZIP bomb (> 25 MB uncompressed) rejected before openpyxl opens it
    bomb_buf = BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/worksheets/sheet1.xml", "A" * (26 * 1024 * 1024))
    bomb_bytes = bomb_buf.getvalue()
    assert len(bomb_bytes) < 200_000  # tiny compressed size, huge uncompressed size!

    r_bomb = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(bomb_bytes), "bomb.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_bomb.status_code == 400
    assert "uncompressed" in r_bomb.get_json()["error"].lower()

    # 4. Token isolation between two admin accounts
    c.post(
        "/api/users",
        json={"username": "staff2", "displayName": "Staff 2", "password": "Staff2Password123!", "role": "admin"},
        headers=headers,
    )
    valid_xlsx = _make_xlsx(
        [
            ["Room", "AgeGroup", "Day", "StartTime", "EndTime", "ClassTitle"],
            ["101", "Adults", "Sunday", "09:00", "10:00", "Valid Class"],
        ]
    )
    token = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(valid_xlsx), "valid.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    ).get_json()["previewToken"]

    c2 = app_instance.test_client()
    h2 = _login(c2, "staff2", "Staff2Password123!")
    steal = c2.post("/api/import/commit", json={"previewToken": token}, headers=h2)
    assert steal.status_code == 403


def test_strict_validation_errors_and_overlap_warnings(admin_session):
    c, headers = admin_session

    # Missing required columns
    missing_cols = _make_xlsx([["Room", "ClassTitle"], ["101", "Only Two Cols"]])
    r_miss = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(missing_cols), "missing.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert r_miss.status_code == 200
    d_miss = r_miss.get_json()
    assert d_miss["canCommit"] is False
    assert d_miss["previewToken"] is None
    assert "Missing required columns" in d_miss["errors"][0]["message"]

    # Bad row values (no silent fallbacks!)
    bad_rows = _make_xlsx(
        [
            ["Room", "AgeGroup", "Day", "StartTime", "EndTime", "ClassTitle", "Cancelled"],
            ["101", "Martians", "Funday", "badtime", "09:00", "Broken Row", "MAYBE"],
            ["101", "Adults", "Sunday", "11:00", "10:00", "Reversed Times", "NO"],
        ]
    )
    r_bad = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(bad_rows), "bad.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    )
    d_bad = r_bad.get_json()
    assert d_bad["canCommit"] is False
    assert d_bad["previewToken"] is None
    err_cols = {(e["row"], e["column"]) for e in d_bad["errors"]}
    assert (2, "AgeGroup") in err_cols
    assert (2, "Day") in err_cols
    assert (2, "StartTime") in err_cols
    assert (2, "Cancelled") in err_cols
    assert (3, "EndTime") in err_cols

    # Overlapping active classes produce a clear warning (non-blocking)
    overlap_xlsx = _make_xlsx(
        [
            ["Room", "AgeGroup", "Day", "StartTime", "EndTime", "ClassTitle", "Leader"],
            ["101", "Adults", "Sunday", "09:00", "10:30", "Class A", "Leader A"],
            ["101", "Adults", "Sunday", "10:00", "11:00", "Class B", ""],
        ]
    )
    d_ov = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(overlap_xlsx), "overlap.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    ).get_json()
    assert d_ov["canCommit"] is True
    assert any("Overlapping active classes" in w["message"] for w in d_ov["warnings"])
    assert any("Missing leader" in w["message"] for w in d_ov["warnings"])


def test_preserve_admin_changes_and_merge_mode(admin_session):
    c, headers = admin_session

    # Add notice + color override on Room 101 and an admin-added class in Room 101
    assert c.patch(
        "/api/rooms/101",
        json={"note": "Moved to Fellowship Hall", "overrideAgeGroupId": "special"},
        headers=headers,
    ).status_code == 200
    admin_cls = c.post(
        "/api/classes",
        json={
            "ageGroupId": "special",
            "roomId": "101",
            "day": "Friday",
            "start": "19:00",
            "end": "20:30",
            "title": "Special Prayer Night",
            "leader": "Pastor Mark",
        },
        headers=headers,
    ).get_json()["class"]

    # Upload only Room 101 in MERGE mode -> Room 102/105/204 stay intact, notice/override/admin class preserved!
    merge_xlsx = _make_xlsx(
        [
            ["Room", "RoomName", "AgeGroup", "Day", "StartTime", "EndTime", "ClassTitle", "Leader"],
            ["101", "Room 101 — Chapel Updated", "Adults", "Sunday", "09:00", "10:15", "New Foundations", "Pastor Mark"],
        ]
    )
    tok = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(merge_xlsx), "merge.xlsx")},
        content_type="multipart/form-data",
        headers=headers,
    ).get_json()["previewToken"]

    res = c.post(
        "/api/import/commit",
        json={
            "previewToken": tok,
            "mode": "merge",
            "preserveRoomNotices": True,
            "preserveRoomOverrides": True,
            "preserveAdminClasses": True,
        },
        headers=headers,
    )
    assert res.status_code == 200
    state = res.get_json()["state"]
    assert "102" in state["roomsMap"]  # Untouched room preserved in merge mode
    assert state["roomsMap"]["101"]["note"] == "Moved to Fellowship Hall"
    assert state["roomsMap"]["101"]["overrideAgeGroupId"] == "special"
    titles_101 = {cl["title"] for cl in state["scheduleList"] if cl["room"] == "101"}
    assert "New Foundations" in titles_101
    assert admin_cls["title"] in titles_101


def test_formula_safe_export_template_and_no_sheetjs_cdn(admin_session):
    c, headers = admin_session

    # Inject formula-like strings into a room note & class title
    assert c.patch("/api/rooms/101", json={"note": "=HYPERLINK(\"http://evil.example\")"}, headers=headers).status_code == 200
    assert c.post(
        "/api/classes",
        json={
            "ageGroupId": "adults",
            "roomId": "101",
            "day": "Thursday",
            "start": "19:00",
            "end": "20:00",
            "title": "+cmd|' /C calc'!A0",
            "leader": "@SUM(1+1)",
        },
        headers=headers,
    ).status_code == 201

    # Export .xlsx and verify formula prefixes are neutralized with leading apostrophe
    exp = c.get("/api/export.xlsx")
    assert exp.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(exp.data))
    assert "Schedule" in wb.sheetnames and "Rooms" in wb.sheetnames
    sched_rows = list(wb["Schedule"].iter_rows(values_only=True))
    titles = [r[6] for r in sched_rows[1:]]
    leaders = [r[7] for r in sched_rows[1:]]
    assert "'+cmd|' /C calc'!A0" in titles
    assert "'@SUM(1+1)" in leaders
    room_rows = list(wb["Rooms"].iter_rows(values_only=True))
    notes = [r[4] for r in room_rows[1:]]
    assert "'=HYPERLINK(\"http://evil.example\")" in notes
    wb.close()

    # Template .xlsx includes live Age Groups Reference tab
    tmpl = c.get("/api/template.xlsx")
    assert tmpl.status_code == 200
    twb = openpyxl.load_workbook(BytesIO(tmpl.data))
    assert "Schedule" in twb.sheetnames and "Age Groups Reference" in twb.sheetnames
    ref_labels = [r[0] for r in list(twb["Age Groups Reference"].iter_rows(values_only=True))[1:]]
    assert "Seniors" in ref_labels and "Adults" in ref_labels
    twb.close()

    # SheetJS CDN completely removed from index.html and CSP
    html = Path("public/index.html").read_text(encoding="utf-8")
    assert "xlsx.full.min.js" not in html
    assert "cdn.jsdelivr.net" not in html
    csp = c.get("/").headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in csp
