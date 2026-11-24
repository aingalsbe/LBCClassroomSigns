# LBC Classroom Signs — Release & Deploy Runbook (`v1.x+`)

A simple, repeatable checklist for shipping future updates (`v1.0.1`, `v1.1.0`, etc.) now that **`v1.0.0`** is live on DigitalOcean (`https://lbcsigns.hipfog.com`).

> **Production Baseline (Already Configured on Droplet)**
> - **Droplet path:** `/root/LBCClassroomSigns`
> - **Compose file:** Untracked `compose.yml` (service/container: `lbcsigns`, `PORT=3002`, `hipfog-network`, `./data:/app/data`) — Docker Compose uses this automatically, so `git pull` stays clean.
> - **Database:** SQLite WAL at `/root/LBCClassroomSigns/data/lbcsigns.db`
> - **`.env` tip:** Always wrap values containing `$` (such as `scrypt` password hashes) in single quotes (`'...'`).

---

## Quick Overview: Who Does What

| Phase | Owner | What Happens |
|---|---|---|
| **1. Build & Verify Locally** | **Antigravity** | Builds code on a feature branch, rebuilds Tailwind CSS if needed, bumps `BUILD_ID`, updates `CHANGELOG.md`, runs `pytest` + JS syntax check, and tests locally in Docker. |
| **2. Local Sign-Off** | **You** | Spot-check the change at `http://localhost:8080` and confirm you're ready to release. |
| **3. Merge, Tag & Push** | **Antigravity** | Merges the feature branch into `main`, tags `vX.Y.Z`, and pushes `main` + the tag to GitHub. |
| **4. Deploy on Droplet** | **You** | SSH into DigitalOcean, take a 1-command SQLite backup, `git pull`, and `docker compose up -d --build`. |
| **5. Verify & Release Note** | **You** | Check `/api/health`, spot-check the live site, and publish the GitHub Release note. |

---

## Step-by-Step Release Checklist

### Phase 1 — Prepare & Test Locally *(Antigravity)*
1. Work on a feature branch (e.g., `feat/...` — never directly on `main`).
2. Rebuild `public/assets/styles.css` if any Tailwind classes changed.
3. Bump `BUILD_ID` in `app/config.py` to `vX.Y.Z` *(triggers wall iPads to auto-refresh cleanly once deployed)* and add the new release notes + GitHub Release URL (`https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/vX.Y.Z`) to `CHANGELOG.md`.
4. Run the full automated test suite (`pytest -v`) and `node --check public/assets/app.js`.
5. Rebuild local Docker (`docker compose up -d --build`) and verify `http://localhost:8080/api/health`.

### Phase 2 — Local Sign-Off *(You)*
1. Open `http://localhost:8080` and verify the new feature/fix looks right.
2. Tell Antigravity: **"Looks good, cut release `vX.Y.Z`."**

### Phase 3 — Merge, Tag & Push to GitHub *(Antigravity)*
1. Merge the feature branch into `main` (`git merge --no-ff`).
2. Create the annotated tag `vX.Y.Z`.
3. Push `main` and `vX.Y.Z` to GitHub.
4. Provide a copy-paste GitHub Release title & description (and flag any `.env` changes).

---

### Phase 4 — Deploy to DigitalOcean *(You)*
SSH into your droplet and run these blocks in order:

#### 1. Back up the live SQLite database
```bash
cd /root/LBCClassroomSigns
mkdir -p ./data/backups
docker compose exec -T lbcsigns python -c "import sqlite3, datetime; ts=datetime.datetime.now().strftime('%Y%m%d-%H%M%S'); s=sqlite3.connect('/app/data/lbcsigns.db'); b=sqlite3.connect(f'/app/data/backups/predeploy-{ts}.db'); s.backup(b); b.close(); s.close(); print('Backup saved:', ts)"
```

#### 2. Pull the new release from GitHub
```bash
git fetch origin --tags
git checkout main
git pull --ff-only origin main
git describe --tags --exact-match
```
*(Confirm that last line prints your new tag, e.g. `v1.1.0`.)*

#### 3. Update `.env` *(only if this release adds/changes an env variable)*
```bash
nano .env
```
*(Skip this step for normal code-only releases.)*

#### 4. Rebuild & restart the container
```bash
docker compose up -d --build
```
*(Wait ~15 seconds for Gunicorn to start.)*

---

### Phase 5 — Verify Production *(You)*

#### 1. Run the 2-line health check on the droplet
```bash
docker inspect --format='{{.State.Health.Status}}' lbcsigns
curl -fsS https://lbcsigns.hipfog.com/api/health
```
Confirm:
- Health status is **`healthy`**.
- `buildId` matches your new **`vX.Y.Z`**, `"journalMode": "wal"`, and `"ok": true`.

*(If anything looks off, check `docker compose logs --tail=40 lbcsigns`.)*

#### 2. Quick live check (30 seconds)
- Open `https://lbcsigns.hipfog.com/` and spot-check the new feature.
- Check one kiosk URL (`https://lbcsigns.hipfog.com/?room=WC%20101&kiosk=1`) — wall iPads pick up the new `buildId` automatically within ~15 seconds.

#### 3. Publish the GitHub Release *(30 seconds)*
Open `https://github.com/aingalsbe/LBCClassroomSigns/releases/new`, select tag **`vX.Y.Z`**, paste the release notes from `CHANGELOG.md`, and click **Publish release**.

---

## Emergency Rollback *(You — Only If Needed)*

### Bad spreadsheet upload only (app is fine)
Sign in at `https://lbcsigns.hipfog.com/` -> **Upload Excel / CSV** -> **Import History & One-Click Rollback** -> **Restore Previous**.

### Roll back app code to the previous tag
```bash
cd /root/LBCClassroomSigns
git checkout v1.0.0   # replace with the last known-good tag
docker compose up -d --build
```

### Restore the pre-deploy SQLite database *(only if a schema/data issue occurred)*
```bash
cd /root/LBCClassroomSigns
docker compose stop lbcsigns
ls -la ./data/backups
cp ./data/backups/predeploy-<TIMESTAMP>.db ./data/lbcsigns.db
rm -f ./data/lbcsigns.db-wal ./data/lbcsigns.db-shm
docker compose start lbcsigns
```

### Reset a locked-out admin password *(anytime)*
```bash
docker compose exec lbcsigns python -m app.cli reset-password --username aingalsbe
```
