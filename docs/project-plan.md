# LBC Room Signs — Project Plan & Rollout

## 1. Completed Build Phases (POC -> Live Deployment)
- **Step 1 — Core iPad Door Display:** Built the room sign UI with color-coded age banners, current/next class logic, and Sunday vs. Wednesday simulator controls.
- **Step 2 — Excel / CSV Uploader:** Added in-browser `.xlsx`/`.xls`/`.csv` parsing (SheetJS), template download, and a schedule preview table supporting 30+ rooms.
- **Step 3 — Admin Control Panel:** Added room search, age-color overrides, custom door notice banners, one-click class cancellations, and inline class/leader edits.
- **Step 4 — Kiosk Mode & Live Clock:** Added `?room=<ID>&kiosk=1` URL routing, 15-second live clock updates, copyable iPad links, and `.xlsx` export.
- **Step 5 — Docker Backend, Security & Live Deployment:** Built the Python/Flask + Gunicorn backend with single-admin session auth, persistent `./data/schedule.json` storage, GitHub version control, and live deployment on DigitalOcean at `https://lbcsigns.hipfog.com`.

## 2. Related POC Assets (in Google Drive)
- `Deliverables/LBC-Room-Signs-POC.pptx` — Executive POC slide deck covering screen mockups, feasibility, and rollout timeline.
- `Deliverables/LBC-Room22-timelapse.mp4` — Time-lapse simulation of Room 22 transitioning through Sunday and Wednesday schedules.

## 3. Classroom Rollout Checklist (30+ iPads)
1. **Prepare Master Spreadsheet:** Export or fill in the `.xlsx` template with all 30+ rooms, Sunday/Wednesday classes, and age groups.
2. **Upload & Verify:** Sign in at `https://lbcsigns.hipfog.com`, upload the spreadsheet, and spot-check rooms in the Admin Panel.
3. **Configure Each iPad:**
   - Join the church Wi-Fi (auto-join enabled).
   - In Safari, open `https://lbcsigns.hipfog.com/?room=<ID>&kiosk=1`.
   - Tap **Share -> Add to Home Screen** so it opens full-screen without Safari toolbars.
   - Set **Settings -> Display & Brightness -> Auto-Lock: Never** and keep plugged into wall power.
   - Enable **Settings -> Accessibility -> Guided Access** with a passcode to lock the iPad onto the sign.

## 4. Future Enhancements (Backlog)
- **All-Rooms Hallway TV Overview (`/tv`):** A single 1920x1080 dashboard showing all 30+ rooms at a glance.
- **Offline Cache (`localStorage`):** Keep displaying the last-known schedule if church Wi-Fi temporarily drops.
- **Date-Specific Exceptions:** Support one-off holiday/event date overrides in the Excel sheet alongside the repeating weekly template.
