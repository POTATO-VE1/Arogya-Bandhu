# AGENTS.md — Aarogya Bandhu

Constitution for any AI model or human building this repo. Read before writing any code.

## Mission (one line)

Post-discharge Kannada IVR follow-up for Karnataka government hospital patients on
feature phones, with an antibiotic-misuse (AMR) surveillance layer.

## Hard rules (non-negotiable)

1. **KISS.** Build only what the docs specify. No extra endpoints, pages, fields,
   dependencies, or "nice improvements" without explicit user approval.
2. **Forbidden unless explicitly approved:**
   - Any phone/dial-pad/number-entry UI on the website (the Demo Call Console is a
     transcript + answer buttons only — see `docs/04_IVR_VOICE.md` §6)
   - Runtime LLM calls in the IVR/call path. Sole exception: Groq `llama-3.3-70b-versatile`
     (via the pinned `httpx` — no SDK dep) for **intake suggestion** and **sheet
     personalization** only, ONLY when `GROQ_API_KEY` is set, ALWAYS degrading to
     deterministic templates on any failure (docs/03 §10)
   - SMS of any kind (TRAI DLT reality — voice only)
   - Chart/graph libraries (stats are hand-rolled bars)
   - Dark/light theme toggle (dark only)
   - Settings/profile/notification pages
   - Auth bypasses or "demo mode" backdoors
   - Sequential integer ids in URLs
   - Patient PII in logs (log ids only)
   - New npm/pip dependencies beyond the pinned list below
3. The docs in `docs/` are the source of truth. Code that disagrees with docs is a bug.
4. Every task must pass its acceptance criteria in `docs/06_BUILD_PLAN.md` before the
   next task starts.

## Stack (pinned)

**Backend** — Python 3.11+, FastAPI, Uvicorn, SQLAlchemy 2.0 (SQLite, WAL mode),
APScheduler 3.x (SQLAlchemyJobStore on the same SQLite file), `twilio` (SDK: TwiML +
signature validation), `edge-tts`, `httpx`, `python-dotenv`, Pydantic v2, `pytest`.
LLM: Groq REST API (OpenAI-compatible) via the already-pinned `httpx` — no SDK dependency.
Password hashing: **stdlib `hashlib.pbkdf2_hmac` only** (no passlib/bcrypt dep).
Sessions: **Starlette `SessionMiddleware`** (signed cookie, itsdangerous ships with
Starlette — no extra dep).

**Frontend** — Node 20+, Vite, React 18 + TypeScript, Tailwind CSS v4,
`react-router-dom`, `@fontsource/ibm-plex-mono`, `@fontsource/noto-sans-kannada`.
Nothing else. No UI kits, no state libraries, no chart libs.

**Infra** — single process serves API + built frontend (`StaticFiles`), SQLite file at
`backend/data/app.db`, `cloudflared`/`ngrok` tunnel for Twilio webhooks during dev/demo.

## Commands

```bash
# setup (once)
python3 -m venv venv && ./venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install

# run (dev)
./venv/bin/uvicorn app.main:app --port 8000 --reload        # from backend/
cd frontend && npm run dev                                   # :5173, proxies /api /ws /audio

# run (prod-ish: one process)
cd frontend && npm run build && cd ../backend && ../venv/bin/uvicorn app.main:app --port 8000

# tests / tooling
cd backend && ../venv/bin/pytest -q
../venv/bin/python -m app.audio.gen_audio          # generate Kannada clips (idempotent)
../venv/bin/python -m app.audio.gen_audio --force  # regenerate all
../venv/bin/python -m app.scripts.seed_demo --reset
```

## Conventions

- **IDs:** uuid4 hex strings, TEXT primary keys, everywhere.
- **Time:** store UTC ISO-8601; render IST in UI; scheduler stores UTC.
- **Errors:** FastAPI default `{"detail": "..."}`; 4xx for client errors, never leak internals.
- **AuthZ:** every `/api/*` route requires a session except `POST /api/auth/login` and
  `GET /api/healthz`. All patient-scoped queries filter by the session's `hospital_code`.
- **Webhooks:** `/webhooks/twilio/*` validated via `X-Twilio-Signature` when
  `TWILIO_VALIDATE_SIGNATURE=1`.
- **Config:** `.env` only; `.env.example` committed; never commit secrets.
- **Audit:** login, enrollment, escalation ack, and demo call triggers write `audit_log` rows.

## Testing rules

- Risk engine and IVR state machine are pure/testable: unit tests required.
- API tests use FastAPI `TestClient` with a real session cookie from a seeded user.
- Twilio webhook tests disable signature validation via env, never via code branches.
- `pytest -q` must be green at the end of every task.

## Definition of done (whole project)

All tasks T1–T17 acceptance criteria pass; `pytest -q` green; `npm run build` clean;
a real Twilio call to a verified phone completes a full question flow; the Demo Call
Console completes the same flow with zero network; seed script produces a demo-ready
board in one command.
