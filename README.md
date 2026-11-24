# LBC Room Signs (Classroom Door Display)

A production-ready, Dockerized digital signage web app for **Lenexa Baptist Church** that turns weekly `.xlsx` / `.csv` schedules into live, color-coded classroom door signs for 30+ wall-mounted iPads.

- **Live URL:** `https://lbcsigns.hipfog.com`
- **iPad Kiosk URL Pattern:** `https://lbcsigns.hipfog.com/?room=<ROOM_ID>&kiosk=1` (e.g. `/?room=WC%20101&kiosk=1`)
- **Project Documentation:**
  - [Requirements](docs/requirements.md)
  - [Project Plan & Rollout Checklist](docs/project-plan.md)
  - [Setup & Deployment Guide (WSL, GitHub, DigitalOcean, Nginx, Backups)](docs/setup-and-deployment.md)

## Key Capabilities (v1.0)
- **Resilient iPad Door Display (`/`):**
  - Public read-only view supporting **Dark (default)** and **Light (`#FAF8F4` warm off-white)** themes toggleable in Admin; the **LBC Church Logo** appears in the main app header across all pages (and stays hidden on wall-mounted door kiosks).
  - Per-class age-group color resolution (multi-age rooms like `WC 101`, `224`, `230`, `232-234` automatically switch banner colors throughout Sunday morning) with optional manual room overrides.
  - Live progress bar (`"XX min left"` / `"Ending soon"`), 30-minute `"Up Next · Starts in X min"` countdown, server-synced `America/Chicago` clock, `ETag` (`304 Not Modified`) polling every 15s, **`localStorage` offline fallback** during Wi-Fi drops, and 100% self-hosted CSS/JS assets (zero external runtime CDNs).
- **Hardened Server-Side `.xlsx` & `.csv` Uploader (`🔒 Admin`):**
  - Server-side `openpyxl` + stdlib `csv` intake with 5 MB size cap, ZIP-bomb & magic-byte checks, strict row-numbered error/warning validation, and a **two-step Preview & Diff -> Confirm Publish** workflow.
  - Supports **Replace** vs. **Merge** modes, preserving live Door Notices, Room Color Overrides, and Admin-edited classes by default, plus **one-click Rollback** (automatic pre-import snapshots of last 10 publishes) and formula-injection-safe `.xlsx` export & live template download.
- **4-Tab Admin Control Panel (`🔒 Admin`):**
  - **Rooms:** Create/rename/delete rooms (even with 0 classes), live Door Notices, Clear All Door Notices, Room Color Overrides, and direct iPad kiosk links.
  - **Classes:** Create/edit/cancel/delete classes, assign rooms or leave unassigned, and atomic **Move / Swap Room** with overlap checking and 8-second Undo.
  - **Age Groups & Colors:** Full DB-backed CRUD with curated WCAG AA swatches + custom `#RRGGBB` hex color picker, live WCAG contrast checker & mini door-sign preview, drag/button ordering, Excel import aliases, and safe atomic reassignment on delete.
  - **Settings, Staff & Activity:** Named multi-admin accounts (`super_admin` / `admin`) with Werkzeug `scrypt` password hashes, immediate session revocation, SQLite-backed rate limiting, CSRF protection, strict `script-src 'self'` CSP, and full per-user Audit Log.
- **SQLite WAL Persistence:** Replaces JSON file writes with multi-worker-safe ACID transactions (`journal_mode=WAL`, `busy_timeout=5000`, `BEGIN IMMEDIATE`) and automatic first-boot migration from `data/schedule.json`.

## Local Quickstart (Docker)
```bash
# 1. Create your .env file from the template (never commit .env)
cp .env.example .env
# Edit .env to set SESSION_SECRET and INITIAL_ADMIN_PASSWORD (or INITIAL_ADMIN_PASSWORD_HASH)

# 2. Start the container (runs as non-root appuser with /api/health check)
docker compose up -d --build
```
Open `http://localhost:8080` (sign in with username `admin` and your configured password) and verify `http://localhost:8080/api/health`.

## Running Automated Tests
```bash
pip install -r requirements-dev.txt
pytest -v
```
