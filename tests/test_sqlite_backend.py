from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app import create_app
from app.db import bump_state_version, connect, init_db, utc_now_iso, write_transaction


@pytest.fixture
def app_instance(tmp_path):
    db_path = tmp_path / "lbcsigns.db"
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "LEGACY_JSON_PATH": tmp_path / "schedule.json",
            "ADMIN_PASSWORD": "test-password",
            "SECRET_KEY": "test-secret-key-for-unit-tests",
            "BUILD_ID": "test-build-1",
        }
    )
    return app


@pytest.fixture
def client(app_instance):
    return app_instance.test_client()


@pytest.fixture
def admin_client(app_instance):
    c = app_instance.test_client()
    res = c.post("/api/login", json={"password": "test-password"})
    assert res.status_code == 200
    return c


def test_health_and_wal_mode(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["journalMode"].lower() == "wal"
    assert data["stateVersion"] >= 1


def test_legacy_json_auto_migration_and_idempotency(tmp_path):
    legacy_path = tmp_path / "schedule.json"
    legacy_data = {
        "roomsMap": {
            "WC 102": {"name": "WC 102 (Pastors Reception)", "ageGroup": "Seniors", "note": "Coffee ready"},
            "224": {"name": "Room 224", "ageGroup": "Adults", "note": ""},
        },
        "scheduleList": [
            {
                "id": 1,
                "room": "WC 102",
                "day": "Sunday",
                "start": "08:00",
                "end": "09:15",
                "title": "Senior Adults 2",
                "leader": "Dick Whittenburg",
                "cancelled": False,
            },
            {
                "id": 2,
                "room": "224",
                "day": "Sunday",
                "start": "09:30",
                "end": "10:45",
                "title": "Family",
                "leader": "Jeremy Combs",
                "cancelled": True,
            },
        ],
    }
    legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")

    db_path = tmp_path / "lbcsigns.db"
    app1 = create_app({"TESTING": True, "DB_PATH": db_path, "LEGACY_JSON_PATH": legacy_path})
    res = app1.test_client().get("/api/state")
    assert res.status_code == 200
    state = res.get_json()
    assert set(state["roomsMap"].keys()) == {"WC 102", "224"}
    assert state["roomsMap"]["WC 102"]["note"] == "Coffee ready"
    assert len(state["scheduleList"]) == 2
    by_title = {c["title"]: c for c in state["scheduleList"]}
    assert by_title["Senior Adults 2"]["ageGroupId"] == "seniors"
    assert by_title["Family"]["ageGroupId"] == "adults"
    assert by_title["Family"]["cancelled"] is True
    assert (tmp_path / "schedule.json.migrated.bak").exists()

    # Second boot must be idempotent and not duplicate rows
    app2 = create_app({"TESTING": True, "DB_PATH": db_path, "LEGACY_JSON_PATH": legacy_path})
    state2 = app2.test_client().get("/api/state").get_json()
    assert len(state2["scheduleList"]) == 2


def test_relational_foreign_key_constraints(app_instance, admin_client):
    # 1. Room can exist with 0 classes
    r_res = admin_client.post(
        "/api/rooms",
        json={"id": "WC 101", "name": "WC 101 (Cafe)", "note": "", "defaultAgeGroupId": None},
    )
    assert r_res.status_code == 201
    assert r_res.get_json()["room"]["id"] == "WC 101"

    # 2. Class MUST have a valid ageGroupId
    bad_class = admin_client.post(
        "/api/classes",
        json={"roomId": "WC 101", "day": "Sunday", "start": "09:30", "end": "10:45", "title": "No Age Group"},
    )
    assert bad_class.status_code == 400

    # Verify DB-level NOT NULL constraint directly
    conn = connect(app_instance.config["DB_PATH"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO classes (age_group_id, room_id, day, start_time, end_time, title, updated_at)
                VALUES (NULL, 'WC 101', 'Sunday', '09:30', '10:45', 'Invalid', ?)
                """,
                (utc_now_iso(),),
            )
    finally:
        conn.close()

    # 3. Class can be created unassigned (roomId = None) with a valid ageGroupId
    unassigned = admin_client.post(
        "/api/classes",
        json={
            "ageGroupId": "seniors",
            "roomId": None,
            "day": "Sunday",
            "start": "08:00",
            "end": "09:15",
            "title": "Senior Adults 7",
            "leader": "Dan Griffin",
        },
    )
    assert unassigned.status_code == 201
    cls = unassigned.get_json()["class"]
    assert cls["roomId"] is None
    assert cls["ageGroupId"] == "seniors"

    # Assign class to WC 101
    assigned = admin_client.patch(
        f"/api/classes/{cls['id']}",
        json={"roomId": "WC 101", "version": cls["version"]},
    )
    assert assigned.status_code == 200
    assert assigned.get_json()["class"]["roomId"] == "WC 101"

    # 4. Deleting an Age Group in use is blocked at DB level (ON DELETE RESTRICT)
    conn = connect(app_instance.config["DB_PATH"])
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM age_groups WHERE id = 'seniors'")
    finally:
        conn.close()

    # 5. Deleting a Room sets classes.room_id = NULL (ON DELETE SET NULL) instead of losing classes
    del_room = admin_client.delete("/api/rooms/WC%20101")
    assert del_room.status_code == 200
    assert del_room.get_json()["unassignedClassCount"] == 1

    unassigned_list = admin_client.get("/api/classes?unassigned=1").get_json()["classes"]
    assert any(c["id"] == cls["id"] and c["roomId"] is None for c in unassigned_list)


def test_multi_age_room_and_natural_sorting(admin_client):
    payload = {
        "roomsMap": {
            "WC 102": {"name": "WC 102 (Pastors Reception)", "ageGroup": "Seniors"},
            "WC 101": {"name": "WC 101 (Cafe)", "ageGroup": "Adults"},
            "232-234": {"name": "Room 232-234", "ageGroup": "Adults"},
            "Foundry": {"name": "The Foundry", "ageGroup": "Teen"},
        },
        "scheduleList": [
            {
                "room": "WC 101",
                "ageGroup": "Seniors",
                "day": "Sunday",
                "start": "08:00",
                "end": "09:15",
                "title": "Senior Adults 7",
                "leader": "Dan Griffin",
                "cancelled": False,
            },
            {
                "room": "WC 101",
                "ageGroup": "Adults",
                "day": "Sunday",
                "start": "09:30",
                "end": "10:45",
                "title": "Empty Nesters 5",
                "leader": "Greg Guthrie & Arlan Suderman",
                "cancelled": False,
            },
            {
                "room": "WC 101",
                "ageGroup": "Seniors",
                "day": "Sunday",
                "start": "11:00",
                "end": "12:15",
                "title": "Senior Adults 4",
                "leader": "Carlos Brewer & Scott Willis",
                "cancelled": True,
            },
        ],
        "preserveRoomOverrides": True,
    }
    res = admin_client.post("/api/schedule/replace", json=payload)
    assert res.status_code == 200
    state = res.get_json()["state"]

    # Verify per-class ageGroupId preserved on WC 101 (not overwritten by last row!)
    wc101_classes = [c for c in state["scheduleList"] if c["room"] == "WC 101"]
    assert [(c["start"], c["ageGroupId"], c["cancelled"]) for c in wc101_classes] == [
        ("08:00", "seniors", False),
        ("09:30", "adults", False),
        ("11:00", "seniors", True),
    ]

    # Natural sort order: numeric rooms before alpha prefixes, WC 101 before WC 102
    room_ids = [r["id"] for r in state["rooms"]]
    assert room_ids == ["232-234", "Foundry", "WC 101", "WC 102"]

    # Set a room notice + override, re-upload with preserveRoomOverrides=True, verify kept
    r_ver = state["roomsMap"]["WC 101"]["version"]
    upd = admin_client.patch(
        "/api/rooms/WC%20101",
        json={"note": "Moved to Fellowship Hall", "overrideAgeGroupId": "special", "version": r_ver},
    )
    assert upd.status_code == 200

    res2 = admin_client.post("/api/schedule/replace", json=payload)
    assert res2.status_code == 200
    wc101_after = res2.get_json()["state"]["roomsMap"]["WC 101"]
    assert wc101_after["note"] == "Moved to Fellowship Hall"
    assert wc101_after["overrideAgeGroupId"] == "special"


def test_etag_polling_and_304(client, admin_client):
    r1 = client.get("/api/state")
    assert r1.status_code == 200
    etag1 = r1.headers["ETag"]
    assert etag1

    r2 = client.get("/api/state", headers={"If-None-Match": etag1})
    assert r2.status_code == 304
    assert r2.data == b""

    # Mutate settings -> stateVersion & ETag must change
    s_res = admin_client.patch("/api/settings", json={"theme": "light"})
    assert s_res.status_code == 200
    assert s_res.get_json()["settings"]["theme"] == "light"

    r3 = client.get("/api/state", headers={"If-None-Match": etag1})
    assert r3.status_code == 200
    assert r3.headers["ETag"] != etag1
    assert r3.get_json()["settings"]["theme"] == "light"


def test_optimistic_concurrency_and_overlap_checks(admin_client):
    state = admin_client.get("/api/state").get_json()
    room = state["roomsMap"]["101"]

    # First edit succeeds (version 1 -> 2)
    ok = admin_client.patch("/api/rooms/101", json={"note": "First edit", "version": room["version"]})
    assert ok.status_code == 200
    assert ok.get_json()["room"]["version"] == room["version"] + 1

    # Second edit using stale version 1 gets 409 VERSION_CONFLICT
    stale = admin_client.patch("/api/rooms/101", json={"note": "Stale edit", "version": room["version"]})
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["current"]["note"] == "First edit"

    # Overlap check on class creation (Room 101 has Sunday 09:00-10:15)
    overlap = admin_client.post(
        "/api/classes",
        json={
            "ageGroupId": "adults",
            "roomId": "101",
            "day": "Sunday",
            "start": "09:30",
            "end": "10:30",
            "title": "Overlapping Class",
        },
    )
    assert overlap.status_code == 409
    assert overlap.get_json()["code"] == "SCHEDULE_OVERLAP"


def test_auth_and_settings_validation(client, admin_client):
    assert client.post("/api/rooms", json={"id": "999", "name": "Nope"}).status_code == 401
    assert client.patch("/api/rooms/101", json={"note": "Nope"}).status_code == 401
    assert client.post("/api/classes", json={"ageGroupId": "adults"}).status_code == 401
    assert client.patch("/api/settings", json={"theme": "light"}).status_code == 401

    assert admin_client.patch("/api/settings", json={"theme": "neon"}).status_code == 400
    assert admin_client.patch("/api/settings", json={"timezone": "Not/A_Zone"}).status_code == 400
    ok = admin_client.patch("/api/settings", json={"theme": "dark", "timezone": "America/Chicago"})
    assert ok.status_code == 200


def test_multithreaded_wal_concurrency(app_instance):
    # Pre-create 8 rooms so 8 writer threads update distinct rooms + 4 reader threads poll concurrently
    setup_client = app_instance.test_client()
    setup_client.post("/api/login", json={"password": "test-password"})
    for i in range(8):
        res = setup_client.post("/api/rooms", json={"id": f"T{i}", "name": f"Thread Room {i}"})
        assert res.status_code == 201

    initial_version = setup_client.get("/api/health").get_json()["stateVersion"]
    writes_per_worker = 10

    def writer(idx):
        c = app_instance.test_client()
        assert c.post("/api/login", json={"password": "test-password"}).status_code == 200
        for step in range(writes_per_worker):
            resp = c.patch(f"/api/rooms/T{idx}", json={"note": f"worker-{idx}-step-{step}"})
            assert resp.status_code == 200, f"Write failed: {resp.status_code} {resp.get_data(as_text=True)}"
        return True

    def reader():
        c = app_instance.test_client()
        for _ in range(25):
            resp = c.get("/api/state")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "roomsMap" in data and "scheduleList" in data
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(writer, i) for i in range(8)] + [pool.submit(reader) for _ in range(4)]
        for f in futures:
            assert f.result() is True

    final_state = setup_client.get("/api/state").get_json()
    assert final_state["stateVersion"] == initial_version + (8 * writes_per_worker)
    for i in range(8):
        assert final_state["roomsMap"][f"T{i}"]["note"] == f"worker-{i}-step-{writes_per_worker - 1}"


def test_multiprocess_wal_concurrency(tmp_path):
    """Simulate 2 Gunicorn OS worker processes writing simultaneously to the same SQLite WAL DB."""
    db_path = tmp_path / "mp_lbcsigns.db"
    init_db(db_path)

    worker_script = (
        "import sys; from pathlib import Path; "
        "from app.db import connect, write_transaction, bump_state_version, utc_now_iso; "
        "db = connect(Path(sys.argv[1])); wid = sys.argv[2]; "
        "n = int(sys.argv[3]);\n"
        "for i in range(n):\n"
        "    with write_transaction(db):\n"
        "        db.execute("
        "            'INSERT INTO rooms (id, name, note, version, updated_at) VALUES (?, ?, ?, 1, ?)', "
        "            (f'P{wid}-{i}', f'Proc {wid} Room {i}', '', utc_now_iso())"
        "        )\n"
        "        bump_state_version(db)\n"
        "db.close()\n"
    )

    project_root = str(Path(__file__).resolve().parent.parent)
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_script, str(db_path), "1", "25"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_script, str(db_path), "2", "25"],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out1, err1 = p1.communicate(timeout=30)
    out2, err2 = p2.communicate(timeout=30)
    assert p1.returncode == 0, f"Worker 1 failed: {err1} {out1}"
    assert p2.returncode == 0, f"Worker 2 failed: {err2} {out2}"

    conn = connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM rooms WHERE id LIKE 'P%'").fetchone()[0]
        assert count == 50
    finally:
        conn.close()
