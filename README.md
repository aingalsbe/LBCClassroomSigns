# LBC Room Signs (Classroom Door Display)

A lightweight, Dockerized web app for **Lenexa Baptist Church** that turns a weekly Excel/CSV schedule into live, color-coded classroom door signs for 30+ wall-mounted iPads.

- **Live URL:** `https://lbcsigns.hipfog.com`
- **iPad Kiosk URL Pattern:** `https://lbcsigns.hipfog.com/?room=<ROOM_ID>&kiosk=1` (e.g. `/?room=101&kiosk=1`)
- **Project Documentation:**
  - [Requirements](docs/requirements.md)
  - [Project Plan & Rollout Checklist](docs/project-plan.md)
  - [Setup & Deployment Guide (WSL, GitHub, DigitalOcean, Nginx)](docs/setup-and-deployment.md)

## Quick Overview
- **Door Display (`/`):** Public read-only view for iPads outside each classroom. Automatically switches between **In Session**, **Cancelled**, and **Room Open**, shows **Up Next**, and polls `/api/state` every 15 seconds.
- **Upload Excel / CSV (`🔒 Admin`):** Upload `.xlsx`, `.xls`, or `.csv` schedules for 30+ rooms, download a ready-made `.xlsx` template, and preview all loaded rows.
- **Admin Panel (`🔒 Admin`):** One-click class cancellations/restores, inline title and leader edits, age-group color overrides, custom door notice banners, direct iPad link copying, and `.xlsx` export.

## Local Quickstart (Docker)
```bash
# 1. Create your .env file (never commit this to GitHub)
cat << EOF > .env
ADMIN_PASSWORD=your-strong-password
SESSION_SECRET=$(openssl rand -hex 32)
EOF

# 2. Start the container
docker compose up -d --build
```
Open `http://localhost:8080` (or `3002` when using the DigitalOcean `compose.yml`).
