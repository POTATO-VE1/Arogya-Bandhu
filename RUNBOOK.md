# RUNBOOK — Aarogya Bandhu

Setup, run, demo, and depart. The one file you read on the morning of the hackathon.

## 0. One-time setup (do this ONCE, today)

```bash
cd ~/Projects/aarogya-bandhu
python3 -m venv venv
./venv/bin/pip install -r backend/requirements.txt
./venv/bin/pip install websockets        # WS client for the demo smoke test (optional)
cd frontend && npm install
npm install-scripts approve esbuild       # only if esbuild postinstall was blocked
cd ..
```

## 1. Run in dev (two processes)

```bash
# terminal 1 — backend
./venv/bin/uvicorn app.main:app --port 8000 --reload --app-dir backend

# terminal 2 — frontend (proxies /api /audio /webhooks /ws → :8000)
cd frontend && npm run dev
# → open http://localhost:5173  (login: admin / changeme123)
```

## 2. Run in "prod-ish" single process (the demo config)

```bash
cd frontend && npm run build              # writes ../backend/static/
cd ../backend && ../venv/bin/uvicorn app.main:app --port 8000
# → open http://localhost:8000  (one process serves API + SPA + /audio + /ws)
```

## 3. Seed a demo-ready board (one command)

```bash
./venv/bin/python -m app.scripts.seed_demo --reset
# users: admin/changeme123, nurse01/nurse1234
# 6 patients across risk states: 1 green, 1 yellow, 1 red+open escalation,
#                                 1 unreachable(×no_answer), 1 fresh pending, 1 red+acked
```

If the board looks stale after a code/model change → `rm backend/data/app.db*` then re-seed.
(`create_all` makes new tables but does NOT add columns to existing ones. This is the
only gotcha you will hit; I have already removed the dev DB whenever I changed a column.)

## 4. Regenerate Kannada audio (after the native-speaker review call)

```bash
./venv/bin/python -m app.audio.gen_audio          # idempotent — skips existing
./venv/bin/python -m app.audio.gen_audio --force  # regenerate all 22 clips
ls backend/data/audio/                            # 22 .mp3 + manifest.json
```

## 5. Tests

```bash
./venv/bin/python -m pytest backend/tests -q      # 53 passed
```

## 6. THE GATE — T8: make a real phone ring (needs your Twilio trial)

This is the only task left and the only one that needs secrets from you. ~10 min.

### 6.1 Create the Twilio trial (₹0)

1. https://www.twilio.com/console → sign up with **your own phone** (it auto-verifies).
   You get **75 free voice minutes**, 30-day trial, calls only to verified numbers (≤5).
2. From the console grab: **Account SID**, **Auth Token**.
3. Phone Numbers → Get a trial number (a US number is fine) → that's `TWILIO_FROM_NUMBER`.
4. Verified Caller IDs → add a **second** number (a teammate's or your second phone)
   that you'll call during the demo. Confirm the SMS code.

### 6.2 Fill `backend/.env`

```bash
cp backend/.env.example backend/.env
```
Edit `backend/.env`:
```
SECRET_KEY=<run: python3 -c "import secrets;print(secrets.token_hex(32))">
ADMIN_PASSWORD=changeme123
HOSPITAL_CODE=KA-DIST-01
HOSPITAL_NAME="District Hospital Demo"
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
TWILIO_VALIDATE_SIGNATURE=1
PUBLIC_BASE_URL=<from step 6.3 — the tunnel URL, no trailing slash>
CALL_ALLOWLIST=+91XXXXXXXXXX,<the second verified number>
# optional polish:
GROQ_API_KEY=<groq free key — for the AI intake-suggest / sheet-personalize demo>
TELEGRAM_BOT_TOKEN=<from @BotFather>     # optional: live red-flag alerts in a group
TELEGRAM_CHAT_ID=<the group chat id>      # optional
```

### 6.3 Start the tunnel (so Twilio can hit your local webhooks)

```bash
# cloudflared (no account, no install drama):
cloudflared tunnel --url http://localhost:8000
# copy the https://<random>.trycloudflare.com URL → that's PUBLIC_BASE_URL (no trailing slash)
```
(Put it in `.env` as `PUBLIC_BASE_URL`, then restart uvicorn so webhooks use it.)

### 6.4 Ring the phone (the actual T8 gate)

```bash
# one process, prod-ish
cd frontend && npm run build && cd ../backend
../venv/bin/uvicorn app.main:app --port 8000
# in another terminal:
cloudflared tunnel --url http://localhost:8000
```
Browser: http://localhost:8000 → login `admin`/`changeme123` → **Intake** →
enroll a patient with the caregiver phone = a verified number, check consent,
click **[ enroll ]** → on the success panel click **[ verify number ]**.

**Gate passes when:** that phone rings, the family presses `1`, and the board
shows `verified ✓` on that row. Then trigger a follow-up call and answer the
Kannada questions by pressing digits — the board risk flips live.

If it doesn't ring, check (in order): tunnel URL in `.env` matches the running
tunnel · `TWILIO_VALIDATE_SIGNATURE=0` temporarily to isolate sig issues ·
`CALL_ALLOWLIST` contains the E.164 number exactly · Twilio console "Latest
Notifications" shows the failed call with a reason.

## 7. The 5-minute demo (rehearse twice)

1. **Intake** on phone (~45s): tiles + meds (note the `[Watch]` AWaRe badge) +
   caregiver number + consent + **[ enroll ]**.
2. Show the **Kannada sheet** (open `/sheet/<enrollment>` → **[ print ]**).
3. Board → row → **[ call ]** (real Twilio) **OR [ sim ]** (the in-browser console
   if venue Wi-Fi dies). Phone rings / console plays clips. Volunteer presses `3`
   (wound: pus/fever) → board flips **[RED]** live.
4. **Escalations** page → **[ ack ]** → `acked by admin · Nm`.
5. **AMR** page → the 5 live KPIs + the pill-count story.
6. Pitch: "₹8–12 per patient · ABDM-ready FHIR · the AI works for the nurse, not
   on the patient" → see docs/07_DEMO_PITCH.md.

## 8. Fallback ladder (test the day before)

1. Real Twilio call to a pre-verified phone. ← primary
2. Demo Call Console `/demo` — same engine, in-browser. ← if venue network is thin
3. Pre-recorded 90s screen capture + the canned audio. ← if total connectivity loss
4. Twilio minutes exhausted → `npm run build`-shipped console-only flow + narration.

## 9. Known gotchas (already handled in code; listed so you don't re-trip)

- `create_all` doesn't add columns to existing tables → `rm backend/data/app.db*` after
  any model change, then re-seed.
- Twilio trial calls only **verified** numbers; this is why `CALL_ALLOWLIST` exists
  (defense in depth — the app refuses to call anything not listed).
- Twilio `X-Twilio-Signature` validation needs the **exact** full URL including query
  string; `PUBLIC_BASE_URL` must match the tunnel URL (no trailing slash) EXACTLY, or
  set `TWILIO_VALIDATE_SIGNATURE=0` while debugging.
- esbuild postinstall can be blocked by npm's allowScripts guard →
  `npm install-scripts approve esbuild`.
- `websockets` is NOT in `requirements.txt` (only needed for the WS smoke-test driver,
  not for the app itself). Install it manually if you want to run that driver.

## 10. Definition of done

- [x] `pytest -q` green (53 passing)
- [x] `npm run build` clean
- [x] seed produces a demo-ready board in one command
- [x] Demo Call Console completes a full question flow with zero network
- [ ] **real Twilio call to a verified phone completes a full question flow** ← T8 gate
- [ ] demo script rehearsed twice end-to-end