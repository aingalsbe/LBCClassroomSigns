# LBC Classroom Signs — Project Plan & Feature Roadmap

- **Current Production Release:** [`v1.0.0`](https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/v1.0.0) (live at `https://lbcsigns.hipfog.com`)
- **Release History:** See [CHANGELOG.md](../CHANGELOG.md)
- **Archived `v1.0` Plan:** See [docs/archive/v1.0-production-readiness-plan.md](archive/v1.0-production-readiness-plan.md)
- **Deployment Checklist:** See [docs/release-runbook.md](release-runbook.md)

---

## 1. Classroom Rollout Checklist (30+ iPads)
1. **Create Staff Accounts & Review Age Groups:** Sign in as `super_admin`, create individual staff `admin` accounts under **Settings, Staff & Activity**, and verify Age Group colors & import aliases under **Age Groups & Colors**.
2. **Upload & Verify Master Schedule:** In **Upload Excel / CSV**, upload the `.xlsx`/`.csv` schedule, inspect the Preview & Diff report (0 errors), and click **Publish Schedule**. Spot-check multi-age rooms (`WC 101`, `224`, `230`, `232-234`) in the Door Display simulator.
3. **Configure Each iPad:**
   - Join the church Wi-Fi (auto-join enabled, portal-free SSID).
   - Open `https://lbcsigns.hipfog.com/?room=<ID>&kiosk=1` in Safari.
   - Tap **Share -> Add to Home Screen** (opens full-screen via `manifest.webmanifest`).
   - Set **Settings -> Display & Brightness -> Auto-Lock: Never** (fixed brightness ~60–70%, wall power connected).
   - Enable **Settings -> Accessibility -> Guided Access** with a passcode to lock the iPad onto the door sign.

---

## 2. Post-`v1.0` Feature & Security Roadmap

### `v1.1.0` — Security, Session & CI Hardening (`feat/security-and-ci-hardening`)
- **Immediate Session Invalidation (`session_version`):** Invalidate all active browser sessions immediately on password change/reset, role downgrade, or "Sign Out All Devices".
- **Server-Side `must_change_password` Enforcement:** Block admin mutations (`403 PASSWORD_CHANGE_REQUIRED`) until temporary passwords are changed via a forced UI modal.
- **Tighter Linux `/app/data` File Permissions:** Use `0o750`/`0o640` when `chown` succeeds on Linux in `docker_entrypoint.py` (keeping permissive fallback only for Windows bind mounts).
- **Security Headers & Login CSRF:** Add HSTS (when HTTPS/secure cookies enabled), `Permissions-Policy`, and CSRF validation on `POST /api/login`.
- **Optional TOTP 2FA:** Opt-in authenticator-app MFA with recovery codes for admins (plus CLI recovery).
- **Audit Log Filtering, CSV Export & Retention:** Filter by user/action/date, export CSV, and prune records older than retention window.
- **GitHub Actions CI:** Automated `pytest`, JS syntax check, Tailwind CSS drift check, and `pip-audit` on every push/PR.

### `v1.2.0` — Date-Specific Exceptions & Holiday Schedules (`feat/date-specific-exceptions`)
- Activate the pre-provisioned `classes.date` (`YYYY-MM-DD`) column with deterministic precedence: **Dated Admin Override > Uploaded Date Row > Recurring Weekly Template**.
- Support optional `Date` column and one-date cancellations/moves in `.xlsx`/`.csv` uploads, preview diff, exporter, and template.
- Admin **Special Dates & Holidays** panel (including one-click campus-wide holiday/snow-day cancellations) and **auto-expiring Room Notices** (`note_expires_at`).

### `v1.3.0` — Hallway TV Overview (`/tv`) & Visitor Directory (`feat/hallway-tv-and-directory`)
- **1920x1080 Hallway TV Dashboard (`/tv`):** 4-column landscape overview grouped by Age Group with live progress bars, automatic 12-second pagination, LBC branding, Dark/Light theme, and per-TV URL filters (`/tv?groups=...`, `/tv?rooms=...`).
- **Mobile Visitor Directory (`/directory`):** QR-code-friendly "Find My Class" search by class title, teacher/leader, room, or age group.

### `v1.4.0` — 30+ iPad Fleet Ops & Broadcast Alerts (`feat/kiosk-fleet-ops`)
- **Live Kiosk Fleet Health Dashboard:** Piggyback lightweight room check-ins on existing 15s polls to flag Online / Stale / Offline iPads and `buildId` versions before Sunday services.
- **Campus-Wide Broadcast / Alert Banner:** High-visibility Info/Warning/Emergency banner across all door iPads and `/tv` screens within 15s.
- **Time-Travel Schedule Simulator:** Preview any future date & time in Admin before Sunday morning.
- **Printable Room QR Setup Sheet** & **Service Worker True-Offline Boot** for full Wi-Fi outage resilience.

### `v2.0+` — Future Backlog
- Planning Center Online (PCO) / ChMS read-only sync.
- Automated encrypted offsite SQLite backup sync (DigitalOcean Spaces / S3).
- Optional idle-room announcement slides.
