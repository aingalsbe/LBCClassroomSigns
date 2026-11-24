from io import BytesIO

import pytest

from app import create_app
from app.services.age_groups import CURATED_PALETTE, contrast_ratio


def _login(client, username="admin", password="TestPassword123!"):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return {"X-CSRF-Token": res.get_json()["csrfToken"]}


@pytest.fixture
def admin_session(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": tmp_path / "m4.db",
            "LEGACY_JSON_PATH": tmp_path / "none.json",
            "INITIAL_ADMIN_USERNAME": "admin",
            "INITIAL_ADMIN_PASSWORD": "TestPassword123!",
            "SECRET_KEY": "m4-test-secret-key-at-least-32-chars!!",
        }
    )
    c = app.test_client()
    return c, _login(c)


def test_curated_palette_meets_wcag_aa():
    for preset in CURATED_PALETTE:
        ratio = contrast_ratio(preset["hex"], "#ffffff")
        assert ratio >= 4.5, f"Preset {preset['name']} ({preset['hex']}) failed WCAG AA: {ratio}:1"


def test_age_groups_crud_contrast_and_reorder(admin_session):
    c, headers = admin_session

    # List with usage counts
    r0 = c.get("/api/age-groups")
    assert r0.status_code == 200
    data0 = r0.get_json()
    assert len(data0["ageGroups"]) == 6
    adults_info = next(g for g in data0["ageGroups"] if g["id"] == "adults")
    assert adults_info["classCount"] > 0

    # Invalid hex rejected
    bad = c.post("/api/age-groups", json={"label": "Bad", "bannerBg": "not-a-hex"}, headers=headers)
    assert bad.status_code == 400

    # Bright yellow auto-selects dark text (#0f172a) for WCAG readability
    cr = c.post(
        "/api/age-groups",
        json={"label": "Preschool", "bannerBg": "#fde047", "aliases": ["Pre-K", "Nursery 2s"]},
        headers=headers,
    )
    assert cr.status_code == 201
    preschool = cr.get_json()["ageGroup"]
    assert preschool["id"] == "preschool"
    assert preschool["bannerText"] == "#0f172a"
    assert preschool["contrastRatio"] >= 10.0
    assert "Pre-K" in preschool["aliases"]

    # Update color to deep navy -> auto-flips bannerText to #ffffff
    upd = c.patch(
        "/api/age-groups/preschool",
        json={"label": "Nursery & Preschool", "bannerBg": "#1e3a8a", "autoText": True},
        headers=headers,
    )
    assert upd.status_code == 200
    u_ag = upd.get_json()["ageGroup"]
    assert u_ag["label"] == "Nursery & Preschool"
    assert u_ag["bannerText"] == "#ffffff"
    assert u_ag["contrastRatio"] >= 8.0

    # Reorder puts preschool first
    all_ids = [g["id"] for g in c.get("/api/age-groups").get_json()["ageGroups"]]
    new_order = ["preschool"] + [gid for gid in all_ids if gid != "preschool"]
    ro = c.put("/api/age-groups/reorder", json={"order": new_order}, headers=headers)
    assert ro.status_code == 200
    assert [g["id"] for g in ro.get_json()["ageGroups"]] == new_order

    # Upload CSV referencing custom alias 'Pre-K' resolves directly to 'preschool'!
    csv_bytes = (
        "Room,RoomName,AgeGroup,Day,StartTime,EndTime,ClassTitle,Leader\n"
        "N1,Nursery A,Pre-K,Sunday,09:30,10:45,Toddlers,Mrs. Rivera\n"
    ).encode("utf-8")
    prev = c.post(
        "/api/import/preview",
        data={"file": (BytesIO(csv_bytes), "nursery.csv")},
        content_type="multipart/form-data",
        headers=headers,
    ).get_json()
    assert prev["canCommit"] is True
    assert prev["previewRows"][0]["ageGroupId"] == "preschool"


def test_age_group_safe_delete_with_reassignment(admin_session):
    c, headers = admin_session

    # Deleting 'adults' (in use by classes) without reassignTo -> 409 REASSIGN_REQUIRED
    blocked = c.delete("/api/age-groups/adults", headers=headers)
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "REASSIGN_REQUIRED"
    assert blocked.get_json()["classCount"] > 0

    # Deleting 'adults' WITH reassignTo='seniors' succeeds atomically
    ok = c.delete("/api/age-groups/adults", json={"reassignTo": "seniors"}, headers=headers)
    assert ok.status_code == 200
    assert ok.get_json()["reassignedTo"] == "seniors"
    assert ok.get_json()["reassignedClasses"] > 0

    state = c.get("/api/state").get_json()
    assert "adults" not in state["ageGroupsMap"]
    assert state["ageGroupsMap"]["seniors"]["isDefault"] is True
    assert all(cl["ageGroupId"] != "adults" for cl in state["scheduleList"])

    # Cannot delete the last remaining age group
    remaining = [g["id"] for g in state["ageGroups"]]
    keep = remaining[0]
    for gid in remaining[1:]:
        r = c.delete(f"/api/age-groups/{gid}", json={"reassignTo": keep}, headers=headers)
        assert r.status_code == 200
    last = c.delete(f"/api/age-groups/{keep}", headers=headers)
    assert last.status_code == 400


def test_clear_all_notices_and_class_swap(admin_session):
    c, headers = admin_session

    # Set notices on two rooms, then clear all in one call
    assert c.patch("/api/rooms/101", json={"note": "Notice A"}, headers=headers).status_code == 200
    assert c.patch("/api/rooms/102", json={"note": "Notice B"}, headers=headers).status_code == 200
    clr = c.post("/api/rooms/clear-notices", headers=headers)
    assert clr.status_code == 200
    assert clr.get_json()["clearedCount"] == 2
    st = c.get("/api/state").get_json()
    assert all(r["note"] == "" for r in st["rooms"])

    # Sunday 09:00-10:15 has class in 101 and class in 102
    cls_101 = next(cl for cl in st["scheduleList"] if cl["room"] == "101" and cl["day"] == "Sunday" and cl["start"] == "09:00")
    cls_102 = next(cl for cl in st["scheduleList"] if cl["room"] == "102" and cl["day"] == "Sunday" and cl["start"] == "09:00")

    # Moving cls_101 to busy Room 102 is blocked with 409 SCHEDULE_OVERLAP
    busy_move = c.post(
        "/api/classes/swap",
        json={"classId": cls_101["id"], "targetRoomId": "102"},
        headers=headers,
    )
    assert busy_move.status_code == 409
    assert busy_move.get_json()["code"] == "SCHEDULE_OVERLAP"

    # Exchanging cls_101 and cls_102 swaps both rooms atomically!
    swapped = c.post(
        "/api/classes/swap",
        json={"classId": cls_101["id"], "exchangeWithClassId": cls_102["id"]},
        headers=headers,
    )
    assert swapped.status_code == 200
    by_id = {cl["id"]: cl for cl in swapped.get_json()["classes"]}
    assert by_id[cls_101["id"]]["room"] == "102"
    assert by_id[cls_102["id"]]["room"] == "101"

    # Moving to a free new room succeeds
    assert c.post("/api/rooms", json={"id": "300", "name": "Room 300 — Empty"}, headers=headers).status_code == 201
    moved = c.post(
        "/api/classes/swap",
        json={"classId": cls_101["id"], "targetRoomId": "300"},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.get_json()["class"]["room"] == "300"
