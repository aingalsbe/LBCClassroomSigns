# Changelog

All notable releases of **LBC Classroom Signs** are documented in this file.
Every tagged release includes a link to its corresponding GitHub Release page.

---

## [v1.0.0](https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/v1.0.0) — Production Release
**GitHub Release:** https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/v1.0.0  
**Tag:** `v1.0.0`

### Overview
First production release of **LBC Classroom Signs**, upgrading the `v0.1-poc` prototype into a resilient, multi-admin, church-ready classroom signage platform with zero runtime CDN dependencies.

### Highlights

#### 1. SQLite WAL Backend & Concurrency
- Replaced flat `schedule.json` writes with an ACID **SQLite (WAL mode)** database supporting concurrent admin edits and simultaneous iPad kiosk polling.
- Automatic, idempotent migration from legacy `schedule.json` on first boot (archived as `schedule.json.migrated.bak`).
- Granular REST APIs with `ETag` / `304 Not Modified` polling, optimistic concurrency conflict detection (`409`), schedule overlap detection, and 8-second Undo on edits.

#### 2. Multi-Admin Security & Audit Logging
- Named multi-admin accounts (`super_admin` and `admin`) with Werkzeug `scrypt` password hashes and CLI recovery tools (`python -m app.cli`).
- Production fail-fast secret checks, `ProxyFix` / Cloudflare real-IP support, SQLite-backed login rate limiting (`429` + `Retry-After`), CSRF token enforcement, and 8-hour sliding secure cookies.
- Full pure-DOM frontend rendering (zero `.innerHTML`), strict Content Security Policy (`script-src 'self'`), and a live per-user **Recent Activity** audit log.

#### 3. Server-Side Excel & CSV Upload Pipeline
- Removed client-side SheetJS CDN in favor of hardened server-side `.xlsx` and `.csv` parsing (`openpyxl`) with 5 MB file caps, ZIP-bomb guards, and magic-byte validation.
- Two-step **Preview -> Commit** workflow with row-numbered error/warning reporting, Replace/Merge modes, and options to preserve live notices, overrides, and admin-created classes.
- One-click **Import History & Rollback** (last 10 snapshots), live `.xlsx` schedule export, and dynamically generated Excel template download.

#### 4. Dynamic Age Groups & Redesigned Admin UX
- Full Age Group management (create, edit, reorder, and safe atomic reassignment on delete) with a WCAG AA (`>= 4.5:1`) color palette, custom hex picker, and live contrast badge.
- Redesigned 4-tab Admin Panel (**Live Schedule & Rooms**, **Age Groups & Colors**, **Tools & Bulk Actions**, **Settings, Staff & Activity**).
- One-click **Clear All Room Notices**, **Swap / Move Classes**, and a live **Dark / Light** Door Display theme toggle.

#### 5. Kiosk Resilience, Branding & Operations
- Self-hosted compiled Tailwind CSS and modular JS (`/assets/styles.css`, `/assets/app.js`) with zero external runtime dependencies.
- LBC church logo added to the main app header (automatically hidden on wall-mounted door kiosks).
- iPad Home Screen PWA manifest, `localStorage` offline fallback, server-synced `America/Chicago` clock, live In-Session progress bar & 30-minute Up Next countdown, and automatic `buildId` reload detection.
- Hardened non-root Docker container with privilege-dropping `/app/data` entrypoint and built-in Docker `HEALTHCHECK` on `/api/health`.

### Upgrade Notes (from `v0.1-poc`)
- Mount `./data:/app/data` in the production Compose file so `lbcsigns.db` persists across rebuilds.
- Configure `APP_ENV=production`, a 64-char `SESSION_SECRET`, `INITIAL_ADMIN_USERNAME`, and a single-quoted `INITIAL_ADMIN_PASSWORD_HASH='scrypt:...'` (generated via `python -m app.cli hash-password`) in `.env`.
- See [docs/setup-and-deployment.md](docs/setup-and-deployment.md) and [docs/release-runbook.md](docs/release-runbook.md) for deployment and backup instructions.

---

## [v0.1-poc](https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/v0.1-poc) — Proof of Concept Baseline
**Tag:** `v0.1-poc`
- Initial working single-file Flask + `schedule.json` + browser SheetJS classroom signage prototype deployed to `https://lbcsigns.hipfog.com`.
