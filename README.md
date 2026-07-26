# Aarogya Bandhu (ಆರೋಗ್ಯ ಬಂಧು)

**Post-discharge follow-up for Karnataka government hospitals.**

A nurse enrolls a discharged patient in under 60 seconds. The system generates a Kannada caregiver protocol and makes automated IVR voice calls on days 1/3/7/14 to any feature phone — no smartphone, no literacy, no internet needed. DTMF answers are scored by a rule-based risk engine; red flags escalate to staff via dashboard + Telegram.

Built-in AMR (antibiotic-misuse) track: adherence verification, pill counts, self-medication screening, stewardship summaries.

## Features

- **Kannada IVR Calls** — Twilio-powered voice calls with edge-tts/Bhashini audio generation
- **DTMF Risk Scoring** — Protocol-driven questionnaires with automatic escalation
- **Nurse Dashboard** — Real-time patient list, search, daily stats, escalation resolution
- **Telegram Bot** — Staff alerts, patient registration, admin commands
- **CSV Patient Import** — Bulk enroll patients from spreadsheet
- **Adherence Timeline** — Color-coded medication tracking per call day
- **Print-Friendly Summary** — Clean discharge summary for paper records
- **Google Fit Integration** — Optional health device data via OAuth2
- **Demo Call Console** — Simulate IVR calls from the browser (no Twilio needed)
- **AMR Stewardship** — Daily reminders, pill counts, weekly summaries

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + SQLAlchemy + SQLite/PostgreSQL |
| Frontend | React 18 + TypeScript + Vite + Tailwind |
| Voice | Twilio IVR + edge-tts (Kannada) |
| Auth | Session cookies (pbkdf2 + salt) |
| Deploy | Docker (Railway / Render / any Docker host) |

## Quick Start

### Local Development

```bash
# Backend
cd backend
cp .env.example .env        # Fill in secrets
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn main:app --port 8000 --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                 # → http://localhost:5173
```

### Docker

```bash
cp backend/.env.example backend/.env   # Fill in secrets
docker compose up --build              # → http://localhost:8000
```

### Deploy to Railway

1. Push this repo to GitHub
2. Connect repo to Railway
3. Set environment variables (copy from `.env.example`)
4. Railway auto-detects the Dockerfile and deploys

## Configuration

All secrets are configured via environment variables. See `backend/.env.example` for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Session encryption key (generate: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `ADMIN_PASSWORD` | Yes | Initial admin login password |
| `DATABASE_URL` | No | SQLite (default) or PostgreSQL connection string |
| `TWILIO_*` | No | Twilio credentials for real IVR calls (demo mode works without) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram alerts for staff |
| `HEALTH_ENCRYPT_KEY` | No | Fernet key for health device tokens |

## Project Structure

```
aarogya-bandhu/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + middleware
│   │   ├── config.py            # Settings (env vars)
│   │   ├── models.py            # SQLAlchemy models
│   │   ├── ivr/                 # IVR engine + Twilio adapter
│   │   ├── routers/             # API routes
│   │   ├── health_fit/          # Google Fit integration
│   │   └── protocols/           # JSON protocol definitions
│   ├── tests/                   # 118 tests
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/               # Board, PatientDetail, Import, Print, etc.
│   │   ├── components/          # Shared UI components
│   │   └── api.ts               # API client
│   └── package.json
├── docs/                        # PRD, Architecture, Protocols, UI Design, etc.
├── Dockerfile                   # Multi-stage build
├── docker-compose.yml           # Local Docker dev
├── railway.json                 # Railway deployment config
├── Procfile                     # PaaS startup
└── start.sh                     # Production entrypoint
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Login |
| GET | `/api/auth/me` | Current user |
| GET | `/api/enrollments` | List patients (with search) |
| POST | `/api/enrollments` | Register patient |
| GET | `/api/enrollments/:id` | Patient detail + call history |
| POST | `/api/escalations/:id/resolve` | Resolve escalation |
| GET | `/api/dashboard/stats` | Daily stats |
| POST | `/api/import/preview` | Preview CSV import |
| POST | `/api/import/confirm` | Execute CSV import |
| POST | `/api/sim/call` | Simulate IVR call |
| GET | `/api/healthz` | Health check |

## License

Built for Arogya Manthan 2K26 hackathon. See `docs/01_PRODUCT.md` for scope.
