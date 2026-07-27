# Aarogya Bandhu — Deployment Guide

This project deploys to **Render** (free tier). Total setup time: ~5 minutes.

## Why Render (not Railway)

- Railway free tier is exhausted on this account.
- Render free tier has no credit-card requirement and supports WebSockets.
- Trade-off: spins down after 15 minutes idle (cold start ~50s). Solved with UptimeRobot pinger.

## What's in this repo

- `Dockerfile` — multi-stage build (React frontend → Python runtime).
- `backend/start.sh` — entrypoint. Re-seeds demo data, then launches uvicorn.
- `render.yaml` — Render Blueprint. One web service + one persistent disk.
- `backend/static/` — built React app (created at build time).

## Step 1 — Connect Render to GitHub

1. Open https://dashboard.render.com/register
2. Sign up with GitHub (cheezy081106@gmail.com if you want to use the same identity as Railway).
3. After signup, Render asks for the GitHub org to install on. Grant access to `POTATO-VE1`.

## Step 2 — Create the Blueprint

1. Go to https://dashboard.render.com/blueprints
2. Click **New Blueprint Instance**
3. Select the repo: **POTATO-VE1/Arogya-Bandhu** (branch: `main`)
4. Render reads `render.yaml` and shows the plan:
   - Service: `aarogya-bandhu` (free web service, Docker, Oregon)
   - Disk: `aarogya-bandhu-data`, 1GB, mounted at `/var/data`
5. Click **Apply**.
6. Render will:
   - Build the Docker image (5–7 min, mostly npm install + pip install)
   - Allocate the persistent disk
   - Generate `SECRET_KEY` and `ADMIN_PASSWORD` automatically
   - Start the service
7. Watch the **Logs** tab. Wait for `INFO: Uvicorn running on http://0.0.0.0:10000` and `Application startup complete.`

## Step 3 — Set PUBLIC_BASE_URL

The first deploy won't have a valid `PUBLIC_BASE_URL` (the Twilio webhook base URL). Set it now:

1. Note the service URL Render assigned. It's in the dashboard top bar, e.g. `https://aarogya-bandhu-xxxx.onrender.com`.
2. Dashboard → **Environment** tab → **Add Environment Variable**:
   - Key: `PUBLIC_BASE_URL`
   - Value: `https://aarogya-bandhu-xxxx.onrender.com` (your actual URL, no trailing slash)
3. Save. Render triggers a redeploy.

## Step 4 — Smoke test

Run from your terminal:

```bash
URL="https://aarogya-bandhu-xxxx.onrender.com"   # your actual URL

# healthz
curl -s $URL/api/healthz
# → {"ok":true,"hospital":"District Hospital Demo"}

# frontend
curl -s -o /dev/null -w "%{http_code}\n" $URL/
# → 200

# login (admin password is auto-generated; check Environment tab)
curl -s -X POST $URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<ADMIN_PASSWORD>"}'
# → {"id":"...","display_name":"District Admin",...}
```

## Step 5 — Keep the service warm (anti-sleep)

Render free tier spins down after **15 minutes** of no HTTP traffic. To prevent the
~50 second cold start during a judge demo, set up an external pinger.

### Recommended: UptimeRobot (free)

1. Open https://uptimerobot.com/signUp
2. Sign up with your email
3. Click **+ Add New Monitor**
4. Monitor Type: **HTTP(s)**
5. Friendly Name: `aarogya-bandhu-keep-alive`
6. URL: `https://aarogya-bandhu-xxxx.onrender.com/api/healthz`
7. Monitoring Interval: **5 minutes** (the free tier minimum)
8. Click **Create Monitor**

That's it. UptimeRobot will ping every 5 minutes, keeping the service warm.

### Alternative: local cron on your laptop (no signup needed)

If you don't want to sign up for UptimeRobot, you can run a small systemd user
service on this laptop (Cachyos) that pings the URL. This works ONLY when your
laptop is on and awake.

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/aarogya-keepalive.service <<'EOF'
[Unit]
Description=Aarogya Bandhu keep-alive pinger
[Service]
Type=oneshot
Environment=URL=https://aarogya-bandhu-xxxx.onrender.com
ExecStart=/usr/bin/curl -fsS -o /dev/null $URL/api/healthz
EOF

cat > ~/.config/systemd/user/aarogya-keepalive.timer <<'EOF'
[Unit]
Description=Ping Aarogya Bandhu every 5 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now aarogya-keepalive.timer
systemctl --user list-timers aarogya-keepalive.timer   # confirm scheduled
```

To stop the pinger later: `systemctl --user disable --now aarogya-keepalive.timer`

## Cold start behavior — what to tell the judges

If 15+ minutes pass between the last pinger and a judge opening the URL, the
first request will take ~30-50 seconds (Render spinning the container back up).
The first hit will return 200 once it's up. **Prewarm before the demo:**

```bash
curl https://aarogya-bandhu-xxxx.onrender.com/api/healthz
# wait for {"ok":true,...}
```

Add a line to your pitch script: "The URL takes about a minute the first
time you load it — that's the free tier spinning up. Subsequent loads are
instant."

## Login credentials

- Username: `admin`
- Password: check Render dashboard → **Environment** tab → `ADMIN_PASSWORD` (auto-generated)
- Superadmin: `root` / `<SUPERADMIN_PASSWORD>` — wait, we didn't set this. Superadmin
  is optional and won't exist by default. To create one, add `SUPERADMIN_PASSWORD`
  to the env vars and redeploy.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Build fails on `npm ci` | `package-lock.json` out of sync | Delete `package-lock.json` locally, `npm install`, commit, push |
| Build fails on `pip install` | Network blip | Click **Manual Deploy → Clear build cache & deploy** in Render dashboard |
| `Application not found` on the URL | First deploy not done yet | Wait 5–7 min, check Logs tab |
| `/api/healthz` returns 200 but `/` returns 404 | React build not copied | Check Dockerfile stage 1 ran `npm run build` successfully |
| Login fails with "demo patients missing" | Database not seeded | `SEED_DEMO=0` was set; remove it from env, redeploy |
| WebSocket disconnects mid-demo | Cold start or 5-min idle | Set up UptimeRobot pinger (Step 5) |

## Updating the deploy

```bash
# local changes
git add -A
git commit -m "your message"
git push origin main
# Render auto-deploys (autoDeploy: true in render.yaml)
# watch: https://dashboard.render.com/...
```

## Database

SQLite at `/var/data/app.db` (on the persistent disk). **Backups are your
responsibility.** To export:

```bash
# from the dashboard "Shell" tab (free tier has this)
sqlite3 /var/data/app.db ".backup /tmp/backup.db"
# then download /tmp/backup.db via the shell tab
```

## Cost summary

- Render free web service: $0
- Render 1GB persistent disk: $0 (free tier includes 1GB)
- UptimeRobot free plan: $0
- **Total: $0/month**

Free tier catches up after 90 days (some limits), and the persistent disk
is "free for now" per Render's current terms. After 90 days, expect a
prompt to switch to a paid plan.
