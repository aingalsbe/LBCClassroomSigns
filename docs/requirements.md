# LBC Room Signs — Requirements

## 1. Project Goal
Provide an easy-to-update digital signage system for **30+ classroom doors** using wall-mounted iPads. Instead of generating and pushing static images, each iPad opens a room-specific web URL that updates automatically based on the weekly schedule and live admin overrides.

## 2. Core Functional Requirements
1. **Excel / CSV Weekly Schedule Upload**
   - Support `.xlsx`, `.xls`, and `.csv` uploads directly in the browser.
   - Expected columns: `Room`, `RoomName`, `AgeGroup`, `Day`, `StartTime`, `EndTime`, `ClassTitle`, `Leader`.
   - Include a downloadable `.xlsx` template and a live preview table of all parsed rows.
   - Support schedules that differ by day of the week (e.g., Sunday morning vs. Wednesday evening).

2. **Per-Room iPad Door Display (30+ Rooms)**
   - Each room has a dedicated URL (`/?room=<ID>&kiosk=1`) that locks the screen to that room in full-screen kiosk mode.
   - Displays **Room Name**, **Age Group Badge**, **Status** (`In Session`, `Cancelled`, `Room Open`), **Current Class** (title, time window, leader), **Up Next**, and **Live Clock / Day**.
   - Automatically refreshes from the server every 15 seconds so changes made by an admin appear without touching any iPad.

3. **Age-Group Color Coordination & Overrides**
   - Color-coded top banners by age group:
     - **Adults** (Indigo)
     - **College** (Teal)
     - **Teen** (Amber)
     - **Kids** (Sky Blue)
     - **Special Event** (Purple)
   - Admins can override any room's age-group color at any time without re-uploading the spreadsheet.

4. **Admin Control Panel**
   - **One-Click Cancellations:** Mark a class as cancelled (shows a red `Cancelled` badge and strikethrough on that room's iPad).
   - **Individual Screen Edits:** Edit class titles or leaders inline, or post a custom **Door Notice** banner (e.g., *"Moved to Hall B"*).
   - **Search & Filter:** Filter rooms and classes quickly across 30+ rooms, copy any room's direct iPad link, and export the updated schedule back to `.xlsx`.

5. **Single-Admin Security**
   - **Public Read-Only Displays:** All 30+ iPads can read `/api/state` and view Door Displays without logging in.
   - **Protected Admin Controls:** Both **Upload Excel / CSV** and **Admin Panel** (and `POST /api/state`) require signing in with `ADMIN_PASSWORD`, verified server-side using constant-time comparison (`hmac.compare_digest`) and stored in an HTTP-only signed session cookie.

## 3. Non-Functional & Hosting Requirements
- **Zero App Store Overhead:** Runs in Safari (Add to Home Screen / Guided Access) on any iPad.
- **Persistent Storage:** Schedule state and admin overrides persist in `./data/schedule.json` via a Docker volume (`/app/data`).
- **HTTPS Encryption:** Served behind Nginx + Cloudflare SSL at `https://lbcsigns.hipfog.com`.
