# LBC Room Signs — Setup & Deployment Guide (v1.0)

## 1. Architecture Summary
- **Frontend:** Self-hosted HTML + compiled Tailwind CSS (`/assets/styles.css`) + modular vanilla JS (`/assets/app.js`) + `manifest.webmanifest` + `/assets/LBC-logo.png` (zero runtime external CDNs).
- **Backend:** Python 3.12 + Flask + Gunicorn (`server:app`, non-root `appuser`, Docker `HEALTHCHECK` on `/api/health`).
- **Persistence:** SQLite in WAL mode at `/app/data/lbcsigns.db` mounted to `./data` on the host (auto-migrates legacy `schedule.json` on first boot and saves `schedule.json.migrated.bak`).
- **Reverse Proxy:** Nginx container on `hipfog-network` + Cloudflare SSL terminating HTTPS for `lbcsigns.hipfog.com`.

## 2. Local Development (Windows / WSL 2 / Docker Desktop)
1. Copy `.env.example` to `.env` (never commit `.env`):
   ```bash
   cp .env.example .env
   ```
   Set `SESSION_SECRET` (e.g., `openssl rand -hex 32`) and `INITIAL_ADMIN_PASSWORD` (for local dev).
2. Run locally with Docker:
   ```bash
   docker compose up -d --build
   ```
   Open `http://localhost:8080` and verify `http://localhost:8080/api/health` returns `"journalMode": "wal"`.
3. Rebuild self-hosted Tailwind CSS after modifying utility classes:
   ```bash
   npx tailwindcss@3.4.17 -i src/tailwind.css -o public/assets/styles.css --minify
   ```
4. Run automated tests:
   ```bash
   pip install -r requirements-dev.txt
   pytest -v
   ```

## 3. Admin CLI Utilities
Run inside the container (or local `.venv`) for initial hashing or emergency recovery:
```bash
# Generate a Werkzeug scrypt password hash for .env (INITIAL_ADMIN_PASSWORD_HASH)
docker compose exec lbcsigns python -m app.cli hash-password

# Create a new super_admin or admin account directly in SQLite
docker compose exec lbcsigns python -m app.cli create-admin --username justin --display-name "Justin Pedigo" --role admin

# Reset a locked-out user's password and clear rate-limit lockouts
docker compose exec lbcsigns python -m app.cli reset-password --username admin
```

## 4. DigitalOcean Production Deployment (`lbcsigns.hipfog.com`)
### `compose.yml`
```yaml
services:
  lbcsigns:
    build: .
    container_name: lbcsigns
    restart: unless-stopped
    env_file:
      - .env
    environment:
      PORT: "3002"
      APP_ENV: "production"
    expose:
      - "3002"
    volumes:
      - ./data:/app/data
    networks:
      - hipfog-network

networks:
  hipfog-network:
    external: true
```

### `.env` on the Droplet
```bash
APP_ENV=production
PORT=3002
SESSION_SECRET=<64-char-random-hex-from-openssl-rand-hex-32>
INITIAL_ADMIN_USERNAME=aaron
INITIAL_ADMIN_DISPLAY_NAME="Aaron Ingalsbe"
INITIAL_ADMIN_PASSWORD_HASH=<scrypt-hash-from-app-cli-hash-password>
COOKIE_SECURE=true
SESSION_LIFETIME_HOURS=8
TRUSTED_PROXY_COUNT=1
TRUST_CLOUDFLARE=true
CHURCH_TIMEZONE=America/Chicago
GUNICORN_WORKERS=2
```

> **Volume permissions note:** Because the container runs as non-root `appuser` (UID `1000`), ensure `./data` on the host is writable by UID `1000` (`chown -R 1000:1000 ./data`) before starting the updated container.

### Nginx (`default.conf` server block)
- Listens on `80` (redirects to `443`) and `443 ssl` for `lbcsigns.hipfog.com`.
- Passes `X-Forwarded-For`, `X-Forwarded-Proto`, and `Host` headers to `http://lbcsigns:3002` over `hipfog-network`.
- Cloudflare DNS: Proxied **A** record for `lbcsigns` -> Droplet IP, SSL/TLS set to **Full (strict)**.

### Deploying Updates on the Droplet
```bash
# 1. Take a quick consistent SQLite backup before deploying
mkdir -p ./backups
docker compose exec -T lbcsigns python -c "import sqlite3; src=sqlite3.connect('/app/data/lbcsigns.db'); dst=sqlite3.connect('/app/data/backup-predeploy.db'); src.backup(dst); dst.close(); src.close()"

# 2. Pull & rebuild
git pull
docker compose up -d --build

# 3. Verify health check
docker inspect --format='{{.State.Health.Status}}' lbcsigns
```

### Nightly SQLite Backup Cron (Droplet Host)
Add a daily cron job (`crontab -e`) keeping 14 days of consistent SQLite online backups (safe while WAL is active):
```cron
0 3 * * * docker exec lbcsigns python -c "import sqlite3, datetime; d=datetime.date.today().isoformat(); s=sqlite3.connect('/app/data/lbcsigns.db'); b=sqlite3.connect(f'/app/data/backups/lbcsigns-{d}.db'); s.backup(b); b.close(); s.close()" && find /path/to/LBCClassroomSigns/data/backups -name 'lbcsigns-*.db' -mtime +14 -delete
```
