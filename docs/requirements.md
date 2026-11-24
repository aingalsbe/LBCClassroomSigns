# LBC Room Signs — Requirements (v1.0)

## 1. Project Goal
Provide a secure, concurrency-safe, easy-to-update digital signage system for **30+ classroom doors** using wall-mounted iPads. Each iPad opens a room-specific web URL (`/?room=<ID>&kiosk=1`) that updates automatically based on the weekly schedule, per-class age groups, and live admin overrides.

## 2. Core Functional Requirements
1. **Hardened Server-Side `.xlsx` & `.csv` Schedule Upload**
   - Supports `.xlsx` and `.csv` files (max 5 MB, ZIP-bomb & magic-byte guarded via `openpyxl` read-only mode + stdlib `csv`; legacy `.xls` rejected).
   - Required columns: `Room`, `AgeGroup`, `Day`, `StartTime`, `EndTime`, `ClassTitle`; optional columns: `RoomName`, `Leader`, `Cancelled`, `Topic`, `Note`, `Date`.
   - Two-step **Validate & Preview Diff -> Confirm Publish** workflow with row-numbered blocking errors and non-blocking warnings (e.g., overlapping classes, missing leaders).
   - Supports **Replace** vs. **Merge** mode, preserves Door Notices, Room Color Overrides, and Admin-edited classes by default, and captures automatic pre-import snapshots (last 10) for **one-click Rollback**.
   - Server-generated `.xlsx` template (including live **Age Groups Reference** tab) and formula-injection-safe `.xlsx` export.

2. **Per-Room iPad Door Display (30+ Rooms)**
   - Dedicated full-screen kiosk URL (`/?room=<ID>&kiosk=1`) with Safari Home Screen `manifest.webmanifest`.
   - Displays **LBC Logo plate** (toggleable), **Room Name**, **Age Group Badge**, **Status** (`In Session`, `Up Next`, `Cancelled`, `Room Open`), **Current Class** (title, time window, leader, live progress bar with `"Ending soon"` <= 5 min), **30-min Up Next countdown**, **Next Class**, and **Server-Synced `America/Chicago` Clock**.
   - Supports admin-toggleable **Dark (default)** and **Light (`#FAF8F4` warm off-white)** themes.
   - Polls `/api/state` every 15 seconds with `ETag` (`304 Not Modified`), auto-reloads on new deploys (`buildId`), and falls back to `localStorage` cache with an `"Offline · showing saved schedule"` footer pill during church Wi-Fi drops.

3. **Relational Data Model & Dynamic Age-Group Color Management**
   - Separate **`age_groups`**, **`rooms`**, and **`classes`** tables in SQLite (WAL mode):
     - Rooms can exist with 0 classes.
     - Every class requires a valid `age_group_id` (`NOT NULL`, `ON DELETE RESTRICT`) and may be assigned to a room (`NULLABLE`, `ON DELETE SET NULL`).
     - Multi-age rooms (`WC 101`, `224`, `230`, `232-234`) dynamically switch banner colors based on the active/upcoming class's age group (precedence: Room Override -> Active/Next Class Age Group -> Room Default -> Neutral).
   - Full Admin CRUD for Age Groups: curated WCAG AA swatches + custom `#RRGGBB` hex picker, automatic WCAG contrast calculation + live mini door-sign preview, display/import priority ordering, Excel import aliases, and atomic reassignment on delete.

4. **Admin Control Panel**
   - **Rooms:** Create/rename/delete rooms, Door Notices, Clear All Door Notices, Room Color Overrides, direct iPad link copy, and preview.
   - **Classes:** Filter by room/unassigned/age group, create/edit/cancel/restore/delete classes, inline edits with 450 ms debounce, optimistic locking (`409` conflict refresh), 8-second Undo, and atomic **Move / Swap Room** with overlap checks.
   - **Settings, Staff & Activity:** Theme & timezone controls, Staff Admin Accounts (`super_admin`), self-service password change, and searchable per-user Audit Log.

5. **Security & Named Multi-Admin Access**
   - **Public Read-Only Displays:** iPads read `/api/state` without signing in.
   - **Named Multi-Admin Auth:** `users` table (`super_admin` / `admin`) with Werkzeug `scrypt` password hashes, immediate session revocation upon deactivation, per-username + per-IP SQLite-backed rate limiting (`429` + `Retry-After`) behind `ProxyFix`, `X-CSRF-Token` enforcement on all mutating endpoints, 8-hour sliding HTTP-only Secure cookies, pure-DOM XSS elimination (zero `.innerHTML`), strict `script-src 'self'` CSP, and fail-fast secret checks in `APP_ENV=production`.

## 3. Non-Functional & Hosting Requirements
- **Zero External Runtime CDNs:** Self-hosted compiled Tailwind CSS (`/assets/styles.css`), JS (`/assets/app.js`), and church logo (`/assets/LBC-logo.png`).
- **Concurrency-Safe Persistence:** SQLite WAL at `/app/data/lbcsigns.db` mounted via Docker volume `./data:/app/data` with automatic `schedule.json` migration.
- **Container Hardening:** Non-root `appuser` in `Dockerfile`, `/api/health` endpoint, Docker `HEALTHCHECK`, and HTTPS behind Nginx + Cloudflare SSL at `https://lbcsigns.hipfog.com`.
