import hmac
import json
import os
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory, session

app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DATA_FILE = Path(os.environ.get("DATA_FILE", "/app/data/schedule.json"))

DEFAULT_STATE = {
    "roomsMap": {
        "101": {"name": "Room 101 — Chapel", "ageGroup": "Adults", "note": ""},
        "102": {"name": "Room 102 — Youth Loft", "ageGroup": "Teen", "note": ""},
        "204": {"name": "Room 204 — College Lounge", "ageGroup": "College", "note": ""},
        "105": {"name": "Room 105 — Kids Zone", "ageGroup": "Kids", "note": ""},
    },
    "scheduleList": [
        {"id": 1, "room": "101", "day": "Sunday", "start": "09:00", "end": "10:15", "title": "Foundations of Faith", "leader": "Pastor Mark Davis", "cancelled": False},
        {"id": 2, "room": "101", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Men's & Women's Roundtable", "leader": "Sarah Jenkins", "cancelled": False},
        {"id": 3, "room": "102", "day": "Sunday", "start": "09:00", "end": "10:15", "title": "High School Morning Hangout", "leader": "Tyler Brooks", "cancelled": False},
        {"id": 4, "room": "102", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Middle School Small Groups", "leader": "Hannah Cole", "cancelled": False},
        {"id": 5, "room": "204", "day": "Sunday", "start": "09:30", "end": "10:45", "title": "College Coffee & Study", "leader": "Alex Rivera", "cancelled": False},
        {"id": 6, "room": "204", "day": "Sunday", "start": "11:00", "end": "12:15", "title": "Young Adults Discussion", "leader": "Jordan Lee", "cancelled": False},
        {"id": 7, "room": "105", "day": "Sunday", "start": "09:00", "end": "10:30", "title": "Elementary Worship & Crafts", "leader": "Emily Watson", "cancelled": False},
        {"id": 8, "room": "105", "day": "Sunday", "start": "10:45", "end": "12:00", "title": "Kids Bible Adventure", "leader": "Emily Watson", "cancelled": False},
        {"id": 9, "room": "101", "day": "Wednesday", "start": "18:00", "end": "19:30", "title": "Midweek Adult Prayer & Study", "leader": "Pastor Mark Davis", "cancelled": False},
        {"id": 10, "room": "102", "day": "Wednesday", "start": "18:15", "end": "19:45", "title": "Wednesday Night Youth Live", "leader": "Tyler Brooks", "cancelled": False},
        {"id": 11, "room": "204", "day": "Wednesday", "start": "18:30", "end": "20:00", "title": "College Midweek Worship", "leader": "Alex Rivera", "cancelled": False},
        {"id": 12, "room": "105", "day": "Wednesday", "start": "18:00", "end": "19:30", "title": "Kids Midweek Club", "leader": "Emily Watson", "cancelled": False},
    ],
    "nextClassId": 13,
}


def load_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        save_data(DEFAULT_STATE)
        return DEFAULT_STATE
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_data(payload):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = DATA_FILE.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp_file.replace(DATA_FILE)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/auth", methods=["GET"])
def auth_status():
    return jsonify({"authenticated": bool(session.get("admin"))})


@app.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))
    if hmac.compare_digest(password, ADMIN_PASSWORD):
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/state", methods=["GET"])
def get_state():
    return jsonify(load_data())


@app.route("/api/state", methods=["POST"])
def update_state():
    if not session.get("admin"):
        return jsonify({"error": "Admin login required"}), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "roomsMap" not in body or "scheduleList" not in body:
        return jsonify({"error": "Invalid schedule payload"}), 400
    save_data(body)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
