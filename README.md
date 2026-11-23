# Classroom Door Display (Docker)

Run this on your laptop for testing or on your DigitalOcean droplet to sync 30+ iPad door displays in real time.

## 1. Run Locally on Your Laptop
```bash
ADMIN_PASSWORD="your-strong-password" docker compose up -d --build
```
Open `http://localhost:8080` in your browser.
- Door displays (`http://localhost:8080/?room=101&kiosk=1`) are public and auto-refresh every 15 seconds.
- **Upload Excel / CSV** and **Admin Panel** require your `ADMIN_PASSWORD`.

## 2. Deploy to DigitalOcean Droplet
1. Copy this folder to your droplet (`scp -r classroom-display-docker root@YOUR_DROPLET_IP:~/classroom-display`).
2. SSH into your droplet and set strong secrets:
   ```bash
   export ADMIN_PASSWORD="your-strong-password"
   export SESSION_SECRET="$(openssl rand -hex 32)"
   docker compose up -d --build
   ```
3. **Important for Admin Security:** Put a reverse proxy with free HTTPS (like **Caddy** or **Nginx + Certbot**) in front of port `8080` so your admin password is encrypted over Wi-Fi.
