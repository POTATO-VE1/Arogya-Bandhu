# 02 — Architecture

## 1. Principles

1. **KISS** — one process, one database file, no framework-within-a-framework.
2. **Vertical slice first** — one protocol works end-to-end before anything replicates.
3. **Provider swap via one seam** — the IVR engine is provider-agnostic; Twilio and the
   Demo Call Console are two thin transports over the same state machine.
4. **Stateless webhooks** — all call state lives in SQLite, never in process memory, so
   the app can restart mid-call-flow and scale horizontally later.
5. **Content is data** — protocols and Kannada scripts are JSON, editable without code.

## 2. Stack & scale path

| Concern | Hackathon (build this) | Scale path (document, don't build) |
|---|---|---|
| API | FastAPI (one process) | Stateless → run N replicas behind a load balancer |
| DB | SQLite WAL via SQLAlchemy 2.0, `DATABASE_URL` env | Same models on Postgres — change the URL only |
| Jobs | APScheduler 3.x, SQLAlchemyJobStore (same SQLite) | Swap for Celery+Redis behind `CallScheduler` class |
| Voice | Twilio trial + Demo Call Console (WS) | Indian CPaaS (Exotel) implementing same webhook contract |
| Audio | Static MP3 files under `/audio` | Move to S3/CDN; manifest already has stable URLs |
| Frontend | React+Vite build served by FastAPI `StaticFiles` | Serve via nginx/CDN; API stays identical |
| Tenancy | Single hospital via `hospital_code` column on every scoped row | Real multi-tenant by adding hospitals table + admin UI |
| Auth | Signed-cookie session, pbkdf2 | SSO/OIDC for hospital systems later |

## 3. Folder tree (create exactly this)

```
aarogya-bandhu/
├── docs/                        # this pack
├── venv/                        # python venv (gitignored)
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── data/                    # app.db + audio clips (gitignored)
│   ├── app/
│   │   ├── main.py              # app factory, middleware, static mount, router includes
│   │   ├── config.py            # pydantic-settings, reads .env
│   │   ├── db.py                # engine, SessionLocal, Base, get_db dependency
│   │   ├── models.py            # all SQLAlchemy models (§5)
│   │   ├── security.py          # pbkdf2 hash/verify, session helpers, rate limiter
│   │   ├── deps.py              # current_user dependency, role guard
│   │   ├── audit.py             # write_audit(db, actor, action, entity, meta)
│   │   ├── protocols/           # protocol JSON files (content, see docs/03)
│   │   │   ├── wound_care.json
│   │   │   ├── antibiotic_course.json
│   │   │   └── fever_viral.json
│   │   ├── protocol_loader.py   # loads+validates protocol JSONs at startup
│   │   ├── risk.py              # pure risk engine (docs/03 §6)
│   │   ├── ivr/
│   │   │   ├── engine.py        # provider-agnostic call state machine (docs/04 §2)
│   │   │   ├── twilio_adapter.py# TwiML builders + signature check
│   │   │   └── sim.py           # Demo Call Console transport over WebSocket
│   │   ├── scheduler.py         # APScheduler setup, call job, retry logic
│   │   ├── notify.py            # Telegram alerts (httpx)
│   │   ├── events.py            # in-process SSE broadcaster
│   │   ├── fhir.py              # FHIR R4 DischargeSummaryRecord export
│   │   ├── llm.py               # Groq assist (docs/03 §10) — flag-gated, template fallback
│   │   ├── audio/
│   │   │   ├── scripts_kn.json  # the Kannada script deck (docs/03 §8)
│   │   │   └── gen_audio.py     # edge-tts generator, idempotent, manifest
│   │   ├── routers/
│   │   │   ├── auth.py          # login/logout/me
│   │   │   ├── enrollments.py   # enrollment, board, patients, sheet, fhir, demo trigger
│   │   │   ├── escalations.py   # list + ack
│   │   │   ├── amr.py           # stewardship summary
│   │   │   └── webhooks.py      # /webhooks/twilio/* + /ws/sim-call
│   │   └── scripts/
│   │       └── seed_demo.py     # --reset: wipes + seeds users/patients/calls
│   └── tests/
│       ├── conftest.py          # test db, seeded session cookie helpers
│       ├── test_risk.py
│       ├── test_ivr_engine.py
│       ├── test_auth.py
│       └── test_api.py
└── frontend/
    ├── package.json
    ├── vite.config.ts           # proxy /api /audio /ws → :8000
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx              # router + auth gate
        ├── api.ts               # fetch wrapper (credentials:'include', error shape)
        ├── theme.css            # design tokens (docs/05 §2)
        ├── components/          # Panel, Stat, RiskBadge, Button, Input, Table, KeyHint, LogLine
        └── pages/
            ├── Login.tsx
            ├── Intake.tsx
            ├── Board.tsx
            ├── PatientDetail.tsx
            ├── Sheet.tsx        # Kannada print sheet
            ├── Escalations.tsx
            ├── Amr.tsx
            └── Demo.tsx         # Demo Call Console
```

## 4. Configuration (`.env.example` — commit this, never the real `.env`)

```bash
DATABASE_URL=sqlite:///./data/app.db
SECRET_KEY=change-me-32-bytes-random           # session signing
HOSPITAL_CODE=KA-DIST-01
HOSPITAL_NAME="District Hospital Demo"
# Twilio (trial)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=                            # trial number, e.g. +1XXXXXXXXXX
TWILIO_VALIDATE_SIGNATURE=1
PUBLIC_BASE_URL=                               # tunnel URL, e.g. https://xyz.trycloudflare.com
# Optional: Bhashini (else edge-tts only)
BHASHINI_API_KEY=
BHASHINI_USER_ID=
# Optional: Groq LLM assist (docs/03 §10). Unset = templates only, nothing breaks.
GROQ_API_KEY=
LLM_MODEL=llama-3.3-70b-versatile
# Optional: Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Demo safety: comma-separated allowlist of callable numbers (E.164). Calls to
# anything else are refused. In trial this mirrors Twilio's verified numbers.
CALL_ALLOWLIST=+91XXXXXXXXXX
```

## 5. Database schema (DDL — implement these tables exactly)

All PKs `TEXT` uuid4 hex. All timestamps `TEXT` UTC ISO-8601 (`now()` helper in db.py).
`hospital_code` on every scoped table — **all queries filter by session hospital** (J7/IDOR).

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,          -- pbkdf2_sha256$iterations$salt_hex$hash_hex
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',   -- 'staff' | 'admin'
  created_at TEXT NOT NULL
);

CREATE TABLE patients (
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  name TEXT NOT NULL,
  age INTEGER,
  sex TEXT,                              -- 'F' | 'M' | 'O'
  abha_number TEXT,                      -- optional, plain text, no verification (roadmap)
  caregiver_name TEXT NOT NULL,
  caregiver_phone TEXT NOT NULL,         -- E.164
  consent_at TEXT NOT NULL,              -- UTC timestamp of verbal consent at desk
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE enrollments (               -- one patient × one protocol episode
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  patient_id TEXT NOT NULL REFERENCES patients(id),
  protocol_id TEXT NOT NULL,             -- e.g. 'wound_care' (JSON file id)
  condition_label TEXT NOT NULL,         -- free-ish short label, e.g. 'Post-op appendectomy'
  ward TEXT,
  discharge_date TEXT NOT NULL,          -- YYYY-MM-DD (IST calendar date)
  status TEXT NOT NULL DEFAULT 'active', -- 'active' | 'completed' | 'lost_to_followup'
  number_verified INTEGER NOT NULL DEFAULT 0,  -- 1 if desk test-call pressed 1 (J3)
  created_at TEXT NOT NULL
);

CREATE TABLE enrollment_meds (           -- meds attached to an enrollment
  id TEXT PRIMARY KEY,
  enrollment_id TEXT NOT NULL REFERENCES enrollments(id),
  med_name TEXT NOT NULL,
  med_type TEXT NOT NULL DEFAULT 'other',-- 'antibiotic' | 'other'
  aware_category TEXT,                   -- 'Access' | 'Watch' | 'Reserve' | NULL
  course_days INTEGER,                   -- for antibiotics: total course length
  doses_per_day INTEGER DEFAULT 3        -- feeds pill-count expectation (J5)
);

CREATE TABLE followup_calls (            -- one row per scheduled/attempted call
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  enrollment_id TEXT NOT NULL REFERENCES enrollments(id),
  day_index INTEGER NOT NULL,            -- 1,3,7,14 (protocol schedule)
  scheduled_at TEXT NOT NULL,            -- UTC
  status TEXT NOT NULL DEFAULT 'pending',
    -- 'pending'|'ringing'|'in_progress'|'completed'|'no_answer'|'failed'|'cancelled'
  attempt INTEGER NOT NULL DEFAULT 1,    -- max 2 (retry +2h once)
  provider TEXT,                         -- 'twilio' | 'sim'
  provider_call_sid TEXT,
  started_at TEXT, completed_at TEXT,
  current_node TEXT,                     -- state machine position (restart-safe)
  risk_level TEXT,                       -- 'green'|'yellow'|'red' (set at completion)
  risk_score INTEGER, risk_reasons TEXT, -- JSON array
  duration_sec INTEGER DEFAULT 0
);

CREATE TABLE call_responses (            -- one row per answered question
  id TEXT PRIMARY KEY,
  call_id TEXT NOT NULL REFERENCES followup_calls(id),
  node_id TEXT NOT NULL,                 -- question id from protocol JSON
  digit TEXT NOT NULL,                   -- '1'..'9'
  score INTEGER NOT NULL,
  answered_at TEXT NOT NULL
);

CREATE TABLE escalations (
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  enrollment_id TEXT NOT NULL REFERENCES enrollments(id),
  call_id TEXT REFERENCES followup_calls(id),
  level TEXT NOT NULL DEFAULT 'red',
  reasons TEXT NOT NULL,                 -- JSON array, human-readable (English)
  status TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'acked'
  acked_by TEXT REFERENCES users(id),
  acked_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE audit_log (                 -- J2: immutable trail, never updated/deleted
  id TEXT PRIMARY KEY,
  hospital_code TEXT NOT NULL,
  actor TEXT NOT NULL,                   -- username or 'system'
  action TEXT NOT NULL,                  -- 'login'|'login_failed'|'enroll'|'ack'|'trigger_call'|'consent'
  entity_id TEXT,
  meta TEXT,                             -- JSON, no PII beyond ids
  created_at TEXT NOT NULL
);
```

`med_catalog` is **not a table** — it is a seed constant in `seed_demo.py` +
`protocols/` (name, type, AWaRe, typical course days). Enrollment meds copy the values
(rows above), so the catalog can change without migration.

## 6. API contract (all JSON; errors `{"detail": str}`)

| Method & path | Auth | Body → Response | Notes |
|---|---|---|---|
| `POST /api/auth/login` | public | `{username, password}` → `{id, display_name, role}` | sets session cookie; rate-limited; audits success+fail |
| `POST /api/auth/logout` | session | — → `204` | clears cookie |
| `GET /api/auth/me` | session | → `{id, display_name, role, hospital_name}` | frontend auth gate |
| `GET /api/protocols` | session | → `[{id, name_en, name_kn, condition, schedule_days}]` | from loaded JSONs |
| `POST /api/enrollments` | staff | `{patient:{name,age,sex,abha_number?,caregiver_name,caregiver_phone}, protocol_id, condition_label, ward?, discharge_date, meds:[{med_name,med_type,aware_category?,course_days?,doses_per_day?}], consent:true}` → `{enrollment_id, patient_id}` | validates phone E.164, `consent` must be true; creates Day 1/3/7/14 `followup_calls`; audits `enroll`+`consent` |
| `POST /api/enrollments/suggest` | staff | `{condition_label, free_text?}` → `{protocol_id, instructions_en[], note}` | LLM intake assist (docs/03 §10); `503 {"detail":"llm disabled"}` when no key; enrollment never depends on it |
| `POST /api/enrollments/{id}/verify-number` | staff | — → `{call_id}` | places **immediate** desk test call (J3); sets `number_verified=1` when family presses 1 |
| `GET /api/board` | session | → `{kpis:{...}, rows:[{enrollment_id, patient_name, protocol_id, day_index_next, last_call_status, last_risk, number_verified, open_escalation}]}` | board page |
| `GET /api/patients/{id}` | session | → full detail: patient, enrollments (incl. `sheet_instructions` — LLM-personalized or template), meds, calls+responses, escalations | 404 if wrong hospital (IDOR-safe) |
| `GET /api/patients/{id}/fhir` | session | → FHIR R4 JSON (download) | docs/03 §9 |
| `GET /api/escalations` | session | → `[{id, patient_name, reasons, status, created_at, acked_by, acked_at, caregiver_phone}]` | open first |
| `POST /api/escalations/{id}/ack` | staff | — → `{status:'acked'}` | sets acked_by/at; audits `ack` (J2) |
| `GET /api/amr/summary` | session | → `{enrolled, reach_rate, course_completion_rate, self_med_rate, median_ack_minutes, call_minutes, est_cost_inr, adherence_buckets:{...}}` | docs/03 §7 |
| `POST /api/demo/trigger-call` | staff | `{enrollment_id, channel:'twilio'|'sim'}` → `{call_id}` | creates+starts an out-of-schedule call now; audits `trigger_call`; refuses numbers outside `CALL_ALLOWLIST` |
| `GET /api/events` | session | SSE stream: `{type:'call_update'|'escalation'|'board', id}` | board live-flip |
| `GET /api/healthz` | public | → `{ok:true}` | |
| `POST /webhooks/twilio/voice/{call_id}` | twilio | form-encoded → TwiML | docs/04 §3 |
| `POST /webhooks/twilio/gather/{call_id}` | twilio | form-encoded (`Digits`) → TwiML | docs/04 §3 |
| `POST /webhooks/twilio/status/{call_id}` | twilio | form-encoded → `204` | retry/unreachable logic |
| `WS /ws/sim-call` | session | Demo Call Console | docs/04 §6 |
| `GET /audio/{clip}.mp3` | public | static file | clips are generic Kannada phrases — **no PII**, safe to leave public |

**Conventions:** `{id}` path params are uuids; wrong-hospital access returns **404**
(not 403 — don't leak existence). List endpoints cap at 200 rows, newest first.

## 7. Security model (implement exactly — no improvisation)

**7.1 Password hashing (stdlib only).** `security.py` must contain:

```python
import hashlib, hmac, os
ITERATIONS = 600_000  # OWASP pbkdf2-sha256 recommendation

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"

def verify_password(pw: str, stored: str) -> bool:
    _, iters, salt_hex, hash_hex = stored.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(dk.hex(), hash_hex)
```

**7.2 Sessions.** Starlette `SessionMiddleware(secret_key=SECRET_KEY, max_age=43200,
same_site='lax', https_only=False)` (https_only flips to True behind a real domain —
env-driven). On login: `request.session['user_id']=...`, `['role']=...`,
`['hospital_code']=...`. `current_user` dependency loads the user or raises 401.

**7.3 Login rate limiting.** In-memory dict keyed `(ip, username)`: after 5 failed
attempts → 15-min lockout → `429 {"detail":"too many attempts, try later"}`.
Single-process is fine (documented scale path: Redis).

**7.4 IDOR / URL mutation protection.** uuid4 ids (unguessable) **and** every
patient-scoped query includes `hospital_code == session.hospital_code`. Mutating an id
in a URL yields 404. There is no cross-hospital endpoint. (Directly answers the user's
"link mutation" concern.)

**7.5 Twilio webhook validation.** When `TWILIO_VALIDATE_SIGNATURE=1`, validate
`X-Twilio-Signature` against the full public URL (`PUBLIC_BASE_URL` + path) and form
params using `twilio.request_validator.RequestValidator`. Invalid → 403.

**7.6 Headers & CORS.** Middleware sets: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Content-Security-Policy: default-src 'self'; media-src 'self'; style-src 'self' 'unsafe-inline'`.
No CORS middleware at all — frontend is same-origin (Vite proxy in dev, StaticFiles in prod).

**7.7 Call safety.** `POST /api/demo/trigger-call` and the scheduler **refuse** any
number not in `CALL_ALLOWLIST` (defense in depth on top of Twilio trial restrictions).
No endpoint accepts a raw "to" number from the client — calls only ever go to the
enrollment's stored caregiver phone. (Prevents the app being abused as a robocaller.)

**7.8 PII hygiene.** Logs contain ids + event names only. Audio clips contain no
patient names. Telegram messages contain patient name + masked phone
(`+91 98•••••210`) + deep link to the escalation page (which requires login).

## 8. Scheduler

- APScheduler `AsyncIOScheduler`, `SQLAlchemyJobStore` on the same SQLite file.
- One job per pending `followup_calls` row at `scheduled_at`; on startup, (re)create
  jobs for all `pending` rows in the future (and fire overdue ones immediately).
- Job = place call via Twilio adapter → set `ringing`. Webhooks drive the rest.
- Calling window guard (J8): jobs due outside 09:00–21:00 IST are deferred to 09:00.

## 9. SSE events

`events.py`: module-level `set[asyncio.Queue]`; `publish(type, id)` fans out;
`/api/events` streams `data: {json}\n\n`, 30s heartbeat comment. Frontend Board and
Escalations pages subscribe and refetch on relevant events. (If SSE proves flaky in
the venue, pages also poll every 5s as fallback — build both, SSE first.)

## 10. Testing strategy

- `test_risk.py` — the 8 cases enumerated in docs/03 §6.4, no DB needed (pure).
- `test_ivr_engine.py` — drive the engine with a fake transport: script digits, assert
  node transitions, scores, red escalation creation.
- `test_auth.py` — login ok/bad/locked; session required on `/api/board`; rate limit 429.
- `test_api.py` — enrollment creates 4 scheduled calls; board shape; escalation ack;
  cross-hospital id returns 404; trigger-call refuses non-allowlisted number.
- Webhook tests: `TWILIO_VALIDATE_SIGNATURE=0` **in test env only**.
