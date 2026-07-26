# 06 — Build Plan (the work queue)

Rules: one task at a time, in order. A task is done only when its **Acceptance**
checks pass. Do not add anything the docs don't specify. When stuck twice, re-read the
referenced doc section and produce a minimal fix — never a silent redesign.

**Prompt pattern to use with an AI model per task:**
> "You are building Aarogya Bandhu. Read `AGENTS.md`, this task, and the referenced
> docs. Implement exactly task **Tn**. Then run the acceptance checks and show output.
> Do not implement anything beyond this task."

---

### T1 — Repo scaffold · ~1h
Backend venv + `requirements.txt` (per AGENTS.md pins) + FastAPI app factory with
`GET /api/healthz`, `config.py` (pydantic-settings), `.env.example` (docs/02 §4),
`data/` dir, frontend Vite+TS+Tailwind v4 scaffold with theme tokens wired (docs/05 §2),
vite proxy for `/api` `/audio` `/ws`.
**Accept:** `/api/healthz` returns `{"ok":true}`; `npm run dev` renders a dark page in
IBM Plex Mono; both servers run simultaneously.

### T2 — Database layer · ~1.5h
`db.py`, `models.py` implementing docs/02 §5 **exactly** (all tables/columns),
`create_all` on startup, `now()` UTC helper.
**Accept:** pytest fixture creates a fresh DB; a script inserts+reads one row per
table; `sqlite3 data/app.db ".schema"` matches the DDL.

### T3 — Auth & security middleware · ~2h (docs/02 §7)
`security.py` (exact pbkdf2 code given), SessionMiddleware, login/logout/me routes,
rate limiter, security headers middleware, `deps.py` guards, seed of one admin user
(`admin` / password from `.env`), audit on login success/fail.
**Accept:** `test_auth.py` green: good login sets cookie, bad login 401, 5 bad → 429,
`/api/board` without session → 401, headers present on responses.

### T4 — Protocol loader + content files · ~1.5h (docs/03 §1–4)
`protocol_loader.py` (load, validate against node graph: all `next` targets exist,
terminals valid, referenced clips exist in `scripts_kn.json`), the three protocol
JSONs, the full `scripts_kn.json` deck.
**Accept:** loader rejects a deliberately broken protocol (bad `next`, missing clip);
`GET /api/protocols` lists 3 protocols.

### T5 — Audio pipeline · ~1.5h (docs/04 §5)
`gen_audio.py` with edge-tts (`kn-IN-SapnaNeural`), manifest, `--force`, idempotency;
static mount `/audio`.
**Accept:** run twice — second run regenerates 0 files; all deck clips exist as
non-trivial MP3s (size > 5KB); one clip plays in a browser.

### T6 — Risk engine · ~1h (docs/03 §6)
`risk.py`, pure.
**Accept:** all 8 enumerated tests green.

### T7 — IVR engine core · ~2.5h (docs/04 §1–2)
`Transport` protocol, `start_call/handle_digit/handle_timeout/finish_call`, node
skipping rules (`requires_antibiotic`, `min_day_vs_course_end`), response persistence,
risk integration, escalation creation + SSE publish.
**Accept:** `test_ivr_engine.py` green with a scripted fake transport, incl.: full
green path; red path creates escalation; timeout→reprompt→no_answer; pill-count node
skipped for med-free enrollment.

### T8 — Twilio adapter + webhooks · ~2h — **HARD GATE** (docs/04 §3)
TwiML builders, the 3 webhook endpoints, signature validation, retry matrix,
`POST /api/demo/trigger-call` (twilio channel) with allowlist.
**Accept (GATE):** with a cloudflared tunnel, a real verified phone rings, plays
Kannada clips, a keypress stores a `call_responses` row, board risk updates. Webhook
tests (signature off) simulate full flows. **Do not proceed until the phone rings.**

### T9 — Enrollment + scheduler · ~1.5h (docs/02 §6, §8)
`POST /api/enrollments` (validation, consent required, med copy, 4 `followup_calls`
rows + jobs), verify-number endpoint, APScheduler with SQLAlchemyJobStore, startup
re-registration, 09:00–21:00 IST guard.
**Accept:** API test: enrollment creates 4 pending calls at correct IST dates;
scheduler fires a due job (use 1-minute offset in test); consent=false → 422.

### T10 — Telegram alerts · ~45m (docs/04 §6)
`notify.py`, exact message format, no-op without token, failure-swallowing.
**Accept:** red in engine test produces one mocked HTTP call; real token produces a
real message.

### T11 — Escalations & audit · ~1h
`GET /api/escalations`, `POST .../ack` with audit rows, dedup-open rule.
**Accept:** ack sets acked_by/at + audit row; cross-hospital id → 404; second red on
same enrollment doesn't duplicate an open escalation.

### T12 — AMR summary API · ~1h (docs/03 §7)
**Accept:** seeded fixture → endpoint returns expected KPI values (assert numbers).

### T13 — FHIR export · ~1.5h (docs/03 §9)
**Accept:** export parses as JSON; contains Composition type `373942005`, Patient,
Encounter, Condition, one MedicationRequest per med; endpoint is hospital-scoped.

### T13b — Groq LLM assist · ~1.5h (docs/03 §10)
`llm.py` (httpx, OpenAI-compatible call, timeout/retry), `POST /api/enrollments/suggest`,
sheet personalization on enrollment (select-by-index into the Kannada bullet bank,
`sheet_instructions` stored with `source`), dosage guard + index validation.
**Accept:** `test_llm.py` green for the full §10.3 failure matrix (mocked httpx);
with key unset, suggest → 503 and sheets render template; with key mocked-good,
`source:"llm"` and bullets resolve only from the bank; enrollment response time
unaffected when the LLM hangs (fake 6s latency).

### T14 — Frontend shell + login + intake · ~3h (docs/05 §3–4, §6.1–6.2)
Router + auth gate (`/api/auth/me`), topbar/statusbar shell, all components,
Login page, Intake page incl. consent checkbox, verify-number button, AWaRe badges.
**Accept:** login/logout round-trip; intake submits a full enrollment and lands on
board; consent unchecked blocks submit; number verify shows result LogLine.

### T15 — Board + patient detail + escalations + AMR pages · ~3h (docs/05 §6.3–6.7)
SSE client with 5s polling fallback, live risk flip, tables per spec.
**Accept:** triggering a red demo call flips the board row to `[RED]` without manual
refresh; escalation ack updates inline; AMR page numbers match `/api/amr/summary`.

### T16 — Sheet + Demo Call Console · ~2.5h (docs/05 §6.5, §6.8; docs/04 §4)
Kannada print sheet + print CSS; `/ws/sim-call` + Demo page (transcript + per-question
answer buttons; NO dial pad, NO number input); sim calls persist like real ones.
**Accept:** a full sim call completes in-browser with audio, creates responses + risk;
sheet prints clean on A4; simulated-call marker visible.

### T17 — Seed script + e2e checklist + rehearsal · ~1.5h
`seed_demo.py --reset`: 2 users (nurse01/admin), 6 patients across protocols/risk
states (1 green done, 1 yellow, 1 red-with-open-escalation, 1 unreachable, 2 fresh),
meds incl. one Watch + one Reserve, realistic Karnataka names/wards. Write
`RUNBOOK.md` (env setup, tunnel, demo steps).
**Accept:** one command produces the demo-ready board; `pytest -q` green;
`npm run build` clean; full demo script (docs/07) rehearsed twice end-to-end.

---

## Time budget

| Block | Tasks | Hours |
|---|---|---|
| Spine | T1–T7 | ~11 |
| Gate + comms | T8–T11 | ~4.5 |
| Data products | T12–T13b | ~4 |
| Frontend | T14–T16 | ~8.5 |
| Polish | T17 | ~1.5 |
| **Total** | | **~29.5** |

## Cut order (when behind schedule)

0. **T13b LLM assist (cut first — it is garnish by design)** → 1. T13 FHIR (mention as
   roadmap instead) → 2. T10 Telegram (show escalation page only) → 3. T16 sheet print
   (keep console; read sheet from screen) → 4. T12 AMR page (hardcode 3 stats from
   seed) — **never cut T8 gate, live risk flip, or the AMR pill-count question.**
