# 04 — IVR, Voice Calls, Demo Call Console, Audio, Telegram

## 1. Design decisions (why it works on a ₹800 feature phone)

- **DTMF only.** Keypad presses — no speech recognition. Works on every phone, in any
  noise, with any Kannada dialect. (ASR via Bhashini is a named roadmap item only.)
- **Pre-generated audio.** All prompts are static MP3s generated offline by
  `gen_audio.py`. Calls have zero runtime dependencies beyond Twilio + static file
  serving. No TTS latency, no mid-call API failure.
- **All state in DB.** The engine reads/writes `followup_calls.current_node` and
  `call_responses` per webhook hit — process restarts don't lose calls.
- **One state machine, two transports.** `ivr/engine.py` is pure logic over a
  `Transport` interface. Twilio webhooks and the Demo Call Console (WebSocket) are the
  two transports. Same code path = the demo can't diverge from production behavior.

## 2. Engine (`ivr/engine.py`)

```python
class Transport(Protocol):
    def play(self, clip_id: str) -> None: ...          # queue audio prompt
    def expect_digit(self, node_id: str, timeout_s: int = 6) -> None: ...
    def hangup(self) -> None: ...

def start_call(db, call_id: str, transport: Transport) -> None: ...
def handle_digit(db, call_id: str, digit: str, transport: Transport) -> None: ...
def handle_timeout(db, call_id: str, transport: Transport) -> None: ...
def finish_call(db, call_id: str, terminal: str) -> None: ...
    # terminal in {'@end_ok','@end_red','@end_noanswer'}
    # → risk.evaluate() → persist risk → maybe escalate + telegram + SSE
```

Flow rules:
- `start_call`: load enrollment + protocol; set node = `start_node`; process nodes of
  `type=play` immediately (queue their clips) until the first `question` node, then
  `expect_digit`. Set `followup_calls.status='in_progress'`, `started_at`.
- `handle_digit`: invalid digit (not in options) counts as a retry (§4); valid digit ⇒
  insert `call_responses` row; if option has `clip`, queue it; advance to `next`;
  skip any question node with `requires_antibiotic` when the enrollment has no
  antibiotic med; skip `min_day_vs_course_end` nodes when `day_index < course_days`;
  on terminal ⇒ `finish_call`.
- `handle_timeout`: per node `retries` (default 1): first timeout ⇒ play
  `timeout_reprompt`, re-expect; exhausted ⇒ `finish_call('@end_noanswer')`.
- `@end_red` ⇒ risk forced red ⇒ escalation (dedup open) + Telegram + SSE.

## 3. Twilio transport (`ivr/twilio_adapter.py` + `routers/webhooks.py`)

### 3.1 Placing a call

```python
client.calls.create(
    to=caregiver_phone, from_=settings.TWILIO_FROM_NUMBER,
    url=f"{PUBLIC_BASE_URL}/webhooks/twilio/voice/{call_id}",
    status_callback=f"{PUBLIC_BASE_URL}/webhooks/twilio/status/{call_id}",
    status_callback_event=["completed", "no-answer", "busy", "failed"],
    timeout=30,
)
```
Refuse if `caregiver_phone` not in `CALL_ALLOWLIST` (defense in depth, docs/02 §7.7).
Store returned SID in `provider_call_sid`.

### 3.2 `POST /webhooks/twilio/voice/{call_id}` — exact TwiML

The engine has already queued clips for the first question. Response shape:

```xml
<Response>
  <Gather numDigits="1" timeout="6" action="{PUBLIC}/webhooks/twilio/gather/{call_id}" method="POST">
    <Play>{PUBLIC}/audio/greet.mp3</Play>
    <Play>{PUBLIC}/audio/confirm_family.mp3</Play>
  </Gather>
  <Redirect>{PUBLIC}/webhooks/twilio/gather/{call_id}?timeout=1</Redirect>
</Response>
```

Rules: every `question` node = one `<Gather>` wrapping the queued `<Play>`s; the
`Redirect` after `Gather` handles no-input (timeout path). Audio URLs are absolute
(`PUBLIC_BASE_URL`).

### 3.3 `POST /webhooks/twilio/gather/{call_id}`

- Has `Digits` param ⇒ `handle_digit`; response TwiML = next question's
  `<Gather>+<Play>`s, or on terminal: optional closing `<Play>` then `<Hangup/>`.
- No `Digits` (timeout redirect hit) ⇒ `handle_timeout`; retry ⇒ same question TwiML
  with `timeout_reprompt.mp3` prepended; exhausted ⇒ `<Hangup/>`.

### 3.4 `POST /webhooks/twilio/status/{call_id}` — retry matrix (J3)

| CallStatus | Action |
|---|---|
| `completed` | nothing (risk already persisted by `finish_call`) |
| `no-answer` / `busy` | if attempt 1: schedule retry **+2h** (attempt=2, status `pending`); if attempt 2: status `no_answer`, increment enrollment's missed counter (feeds risk rule 6.2 + `lost_to_followup` after 3 consecutive) |
| `failed` / `canceled` | same as no-answer but also audit-log |

### 3.5 Signature validation

Per docs/02 §7.5. Validate using the **full URL including query string** and POST
form params. Return 403 on failure.

## 4. Demo Call Console (`ivr/sim.py` + frontend `Demo.tsx`)

**Purpose (only these):** (a) develop without burning Twilio minutes; (b) demo
fallback when the venue network dies. **It is not a phone.** There is **no dial pad,
no number entry, no phone frame** anywhere in the UI.

- Triggered from the Board page: `[ demo call ]` button per enrollment row, and from
  `POST /api/demo/trigger-call {channel:'sim'}` → creates `followup_calls` row
  (`provider='sim'`) and returns `call_id`; frontend opens `/demo?call=<id>`.
- WebSocket `/ws/sim-call?call_id=<id>` (session-authenticated). Server runs
  `start_call` with a WS transport.
- Wire messages (JSON, one per line):
  - s→c `{"type":"play","clip":"greet","en":"Greetings. This is..."}` — UI appends a
    transcript line (clip id + English gloss) and plays `/audio/greet.mp3`.
  - s→c `{"type":"expect_digit","node_id":"q_wound"}` — UI enables answer buttons.
  - c→s `{"type":"digit","digit":"3"}`.
  - s→c `{"type":"end","terminal":"@end_red","risk":"red","reasons":[...]}`.
- UI = terminal-style transcript + three large answer buttons `[ 1 ] [ 2 ] [ 3 ]`
  (buttons are **answer choices**, labeled per current question in English, enabled
  only during `expect_digit`). Full layout: docs/05 §6.8.
- Sim calls write the same `call_responses`, risk, escalations as Twilio calls — the
  dashboard can't tell the difference. Mark `provider='sim'` visibly on call logs.

## 5. Audio pipeline (`app/audio/`)

- `scripts_kn.json` — the deck from docs/03 §8 (single source of truth for all clips).
- `gen_audio.py` (run as `python -m app.audio.gen_audio`):
  1. Load deck; for each clip where `data/audio/<id>.mp3` missing (or `--force`):
  2. Engine order: **edge-tts** voice `kn-IN-SapnaNeural` (female; `--voice` flag can
     override, e.g. `kn-IN-GaganNeural`) → if `BHASHINI_API_KEY` set and `--engine
     bhashini`, call Bhashini TTS pipeline instead → final fallback: fail loudly with
     the clip id (never silently skip).
  3. Write `data/audio/manifest.json`: `{clip_id: {file, chars, generated_at}}`.
- Idempotent; committed clips are **not** required — CI/demo machine regenerates.
- Files served at `/audio/<id>.mp3` via FastAPI static mount (public; no PII in clips).

## 6. Telegram (`notify.py`)

Plain `httpx` POSTs to the Bot API (no telegram SDK):

```python
async def telegram_red(db, escalation) -> None:
    # POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage
    # chat_id=TELEGRAM_CHAT_ID, disable_web_page_preview=True
```

Message format (plain text, parse_mode none — KISS):

```
🔴 RED FLAG — District Hospital Demo
Patient: <name> (enrollment <short-id>)
Protocol: wound_care · Day 3
Reasons: wound: pus/bleeding/fever (SSI red flag)
Caregiver: +91 98•••••210 (masked)
Open: {PUBLIC_BASE_URL}/escalations
Call caregiver: tel:<full number>   ← deep link used by staff, number not shown in group beyond mask
```

- Fire on escalation creation only (not per call), failures logged and swallowed
  (Telegram down must never break a call).
- If `TELEGRAM_BOT_TOKEN` unset: no-op with a one-time startup warning log.

## 7. Scheduler ↔ engine contract

- Scheduler (docs/02 §8) only **places** calls; webhooks/WS drive everything after.
- Enrollment creation inserts `followup_calls` rows for each `schedule_days` entry
  (`scheduled_at = discharge_date + day_index, 10:00 IST`) and registers APScheduler
  jobs; startup re-registers pending rows (restart-safe).
- Demo trigger bypasses the schedule (immediate), still subject to allowlist + window
  guard **except** when `channel='sim'` (console may run anytime, no real call).
