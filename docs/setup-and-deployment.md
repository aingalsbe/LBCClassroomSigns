# LBC Room Signs — Setup & Deployment Guide

## 1. Architecture Summary
- **Frontend:** Single-page HTML + Tailwind CSS + SheetJS (`public/index.html`)
- **Backend:** Python 3.12 + Flask + Gunicorn (`server.py`) listening on port `3002`
- **Persistence:** JSON file at `/app/data/schedule.json` mounted to `./data` on the host
- **Reverse Proxy:** Nginx container on `hipfog-network` terminating SSL for `lbcsigns.hipfog.com`

## 2. Local Development (Windows + WSL 2 + Docker Desktop)
1. In **Docker Desktop -> Settings -> Resources -> WSL Integration**, enable integration for your WSL distro (e.g., `Ubuntu`) and click **Apply & restart**.
2. Create a local `.env` file in the project root (ignored by `.gitignore`):
   ```bash
   ADMIN_PASSWORD=your-local-password
   SESSION_SECRET=local-dev-secret
   ```
3. Run locally:
   ```bash
   docker compose up -d --build
   ```

## 3. GitHub Version Control Workflow
- `.gitignore` excludes `.env`, `data/`, `__pycache__/`, `*.pyc`, and `.DS_Store`.
- Standard update cycle:
  ```bash
  git add .
  git commit -m "Describe your change"
  git push
  ```

## 4. DigitalOcean Production Deployment (`lbcsigns.hipfog.com`)
### `compose.yml`
```yaml
services:
  lbcsigns:
    build: .
    container_name: lbcsigns
    restart: unless-stopped
    command: ["gunicorn", "--bind", "0.0.0.0:3002", "--workers", "2", "server:app"]
    env_file:
      - .env
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
ADMIN_PASSWORD=your-strong-production-password
SESSION_SECRET=64-char-random-hex-from-openssl-rand-hex-32
```

### Nginx (`default.conf` server block)
- Listens on `80` (redirects to `443`) and `443 ssl` for `lbcsigns.hipfog.com`.
- Proxies `/` to `http://lbcsigns:3002` over `hipfog-network`.
- Cloudflare DNS: Proxied **A** record for `lbcsigns` -> Droplet IP, SSL/TLS set to **Full (strict)**.

### Deploying Updates on the Droplet
```bash
git pull
docker compose up -d --build
```
If you only change `.env`, recreate the container:
```bash
docker compose up -d --force-recreate
```
