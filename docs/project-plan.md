# LBC Room Signs — Project Plan & Rollout (v1.0)

## 1. Completed Build Milestones (`v0.1-poc` -> `v1.0.0`)
- **`v0.1-poc` (Baseline Snapshot on `main`):** Working single-file Flask + JSON + browser SheetJS POC deployed to `https://lbcsigns.hipfog.com`.
- **Milestone 1 (`prod/sqlite-backend`):** Modular `app/` architecture, SQLite WAL database (`PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `BEGIN IMMEDIATE`), 3-table relational schema (`age_groups`, `rooms`, `classes`), automatic `schedule.json` migration, granular REST APIs with `ETag`/`304`, optimistic locking (`409`), overlap checks, and multi-thread/multi-process concurrency tests.
- **Milestone 2 (`prod/security-hardening`):** Named multi-admin accounts (`super_admin` / `admin`), operator CLI (`python -m app.cli`), fail-fast production secrets, `ProxyFix`, SQLite-backed username+IP rate limiting, CSRF protection, hardened cookies, pure-DOM XSS elimination (zero `.innerHTML`), security headers, and per-user Audit Log.
- **Milestone 3 (`prod/excel-upload-pipeline`):** Removed client-side SheetJS `0.18.5`, built server-side `.xlsx`/`.csv` validator (`openpyxl` read-only + ZIP-bomb & magic-byte guards), multi-worker-safe two-step Preview & Diff -> Commit workflow, Replace/Merge & Preserve-Admin-Changes options, one-click Rollback snapshots, and formula-safe `.xlsx` export/template.
- **Milestone 4 (`prod/age-groups-and-admin-ux`):** Full Age Groups & Colors Manager (curated WCAG AA swatches + custom hex picker, live WCAG contrast badge & mini door-sign preview, ordering, Excel import aliases, atomic reassignment on delete), 4-tab Admin Panel with Room/Class CRUD, Clear All Door Notices, atomic Move/Swap Room, and Dark/Light Display Theme toggle.
- **Milestone 5 (`prod/kiosk-resilience-and-ops`):** Compiled self-hosted Tailwind CSS (`/assets/styles.css`), extracted `/assets/app.js`, strict `script-src 'self'` CSP, self-hosted LBC logo plate, `manifest.webmanifest`, `localStorage` offline fallback, server-synced `America/Chicago` clock, In-Session progress bar + 30-min Up Next countdown, non-root `Dockerfile` + `HEALTHCHECK`, `.env.example`, and v1.0 docs.

## 2. Classroom Rollout Checklist (30+ iPads)
1. **Deploy `v1.0.0` on DigitalOcean:** Configure `.env` with `APP_ENV=production`, 64-char `SESSION_SECRET`, `INITIAL_ADMIN_PASSWORD_HASH`, `TRUSTED_PROXY_COUNT=1`, and `TRUST_CLOUDFLARE=true` (see [setup-and-deployment.md](setup-and-deployment.md)).
2. **Create Staff Accounts & Review Age Groups:** Sign in as `super_admin`, create individual staff admin accounts under **Settings, Staff & Activity**, and verify Age Group colors & import aliases under **Age Groups & Colors**.
3. **Upload & Verify Master Schedule:** In **Upload Excel / CSV**, upload the `.xlsx`/`.csv` schedule, inspect the Preview & Diff report (0 errors), and click **Publish Schedule**. Spot-check multi-age rooms (`WC 101`, `224`, `230`, `232-234`) in the Door Display simulator.
4. **Configure Each iPad:**
   - Join the church Wi-Fi (auto-join enabled, portal-free SSID).
   - Open `https://lbcsigns.hipfog.com/?room=<ID>&kiosk=1` in Safari.
   - Tap **Share -> Add to Home Screen** (opens full-screen via `manifest.webmanifest`).
   - Set **Settings -> Display & Brightness -> Auto-Lock: Never** (fixed brightness ~60–70%, wall power connected).
   - Enable **Settings -> Accessibility -> Guided Access** with a passcode to lock the iPad onto the door sign.

## 3. Optional Post-v1.0 Backlog (Milestone 6)
- **Hallway TV Overview (`/tv`):** 1920x1080 4-column overview grouped by age group (auto-paging every 12s when overflowing) + searchable interactive directory.
- **One-Off Date Exceptions UI:** Activate the existing `classes.date` column for holiday/event spreadsheet rows and dated admin overrides (`Admin Override > Uploaded Date Row > Weekly Template`).
