# 03 — Protocols, Risk Engine, AMR, Kannada Script Deck, FHIR

A **protocol** is a JSON content file. No Python changes are needed to add or edit a
protocol. The IVR engine (docs/04) interprets it; the risk engine (§6) scores it.

## 1. Protocol file schema

```jsonc
{
  "id": "wound_care",                 // unique, snake_case, = filename
  "version": 1,
  "name_en": "Post-operative wound care",
  "name_kn": "ಶಸ್ತ್ರಚಿಕಿತ್ಸೆ ನಂತರದ ಗಾಯದ ಕಾಳಜಿ",
  "condition": "post_op",
  "schedule_days": [1, 3, 7, 14],      // day_index values, relative to discharge_date
  "start_node": "greet",
  "nodes": { /* §2 */ }
}
```

## 2. Node schema

```jsonc
"greet": {
  "type": "play",            // play a clip, then go to next. No input.
  "clip": "greet",           // clip id in scripts_kn.json
  "next": "confirm_family"
},
"confirm_family": {
  "type": "question",        // play clip, collect ONE digit
  "clip": "confirm_family",
  "retries": 1,              // on timeout/invalid: replay once, then hang up as no_answer
  "options": {
    "1": { "score": 0, "next": "q_day_feeling" },
    "2": { "score": 0, "clip": "wrong_person", "next": "@end_noanswer" }
  }
},
"q_wound": {
  "type": "question",
  "clip": "q_wound",
  "retries": 1,
  "options": {
    "1": { "score": 0, "next": "q_meds_today" },
    "2": { "score": 2, "clip": "counsel_yellow", "next": "q_meds_today",
           "reason": "wound: pain/swelling reported" },
    "3": { "score": 10, "clip": "red_response", "next": "@end_red",
           "reason": "wound: pus/bleeding/fever (SSI red flag)" }
  }
}
```

Node/option fields:
- `type`: `"play"` | `"question"`.
- `clip` (node level): prompt. Option-level `clip`: played **after** the digit is
  recorded (counseling), then flow continues to `next`.
- `score`: integer added to call risk score.
- `reason` (optional): English string, appended to `risk_reasons` (shown on dashboard
  and in escalation/Telegram). Keep reasons clinician-readable.
- `next`: node id, or terminal `"@end_ok" | "@end_red" | "@end_noanswer"`.
- Terminal `@end_red` ⇒ risk forced to red regardless of score; creates escalation.

## 3. Protocol 1 — `wound_care.json` (build this one first; vertical slice)

Flow: `greet → confirm_family → q_wound → q_meds_today → q_pillcount → edu_byte →
closing`. `q_pillcount` only included when the enrollment has an antibiotic
med; otherwise skipped by the engine (engine rule: question nodes may declare
`"requires_antibiotic": true`). Full node list:

| node | type | clip | options → (score, next) |
|---|---|---|---|
| greet | play | `greet` | → confirm_family |
| confirm_family | question | `confirm_family` | 1→(0, q_wound) · 2→(0, clip `wrong_person`, @end_noanswer) |
| q_wound | question | `q_wound` | 1→(0) · 2→(2, clip `counsel_yellow`, reason) · 3→(10, clip `red_response`, reason, @end_red) — all then q_meds_today except red |
| q_meds_today | question | `q_meds_today` | 1→(0) · 2→(2, clip `counsel_adherence`, reason "doses missed today") · 3→(6, clip `counsel_adherence`, reason "meds stopped") → q_pillcount |
| q_pillcount | question, `requires_antibiotic` | `q_pillcount` | 1→(0) · 2→(1, reason "pills remain: 4–7") · 3→(2, reason "pills remain: 8+") → edu_byte |
| edu_byte | play | `edu_amr` | → closing |
| closing | play | `closing` | → @end_ok |

## 4. Protocols 2 & 3 (same schema; replicate after the slice works)

**`antibiotic_course.json`** — `greet → confirm_family → q_symptom_course (better 1 /
same 2 [score 2] / worse 3 [score 10, red_response, @end_red]) → q_meds_today →
q_pillcount → q_self_med (no 1 [0] / yes 2 [3, reason "self-medication reported",
clip counsel_selfmed]) → q_leftover (no 1 [0] / yes 2 [1, clip counsel_leftover,
reason "leftover antibiotics at home"]) → q_course_done* (yes 1 [0] / no 2 [2,
clip counsel_adherence]) → edu_byte → closing`.
*`q_course_done` is asked **only when `day_index >= course_days`** (engine computes
from `enrollment_meds.course_days`; node declares `"min_day_vs_course_end": true`).
If the antibiotic course is shorter than 14 days, schedule becomes `[1,3,7]` (drop 14).

**`fever_viral.json`** — `greet → confirm_family → q_fever (none 1 [0] / mild 2 [2] /
high+chills 3 [10, red_response, @end_red, reason "high fever with rigors"]) →
q_breath (no 1 [0] / yes 2 [10, red_response, @end_red, reason "breathlessness"]) →
q_self_med → edu_viral ("antibiotics don't work on viral fever") → closing`.

## 5. AMR design (the differentiator — J5)

1. **Pill-count verification.** `q_pillcount` gives ranges (single digit, feature-phone
   safe). The engine also computes **expected remaining pills** =
   `max(0, course_days*doses_per_day - days_since_start*doses_per_day)` and stores the
   reported bucket vs expectation in `risk_reasons` when inconsistent
   (e.g., "8+ pills remain on day 6 of a 5-day course ⇒ likely stopped early").
2. **Course-completion question** timed to course end (§4) — not asked blindly.
3. **Self-medication screening** (`q_self_med`) — catches "taking something the doctor
   didn't prescribe", incl. OTC antibiotics.
4. **Leftover-medication screening + counseling** (`q_leftover`) — attacks household
   antibiotic hoarding/sharing.
5. **Education byte on every call** (`edu_amr` / `edu_viral`) — aligns with India's Red
   Line campaign message.
6. **AWaRe tagging at intake** — med catalog carries WHO Access/Watch/Reserve; Watch/
   Reserve meds render an amber badge in the intake UI (awareness nudge, no blocking).

**Med catalog seed** (`seed_demo.py`, copied into `enrollment_meds` at enrollment):

| name | type | AWaRe | course_days | doses/day |
|---|---|---|---|---|
| Amoxicillin 500mg | antibiotic | Access | 5 | 3 |
| Amoxiclav 625mg | antibiotic | Access | 5 | 2 |
| Azithromycin 500mg | antibiotic | Watch | 3 | 1 |
| Ciprofloxacin 500mg | antibiotic | Watch | 5 | 2 |
| Ceftriaxone 1g inj | antibiotic | Watch | 3 | 1 |
| Cefixime 200mg | antibiotic | Watch | 5 | 2 |
| Metronidazole 400mg | antibiotic | Access | 5 | 3 |
| Doxycycline 100mg | antibiotic | Access | 7 | 2 |
| Paracetamol 500mg | other | — | — | 3 |
| ORS | other | — | — | — |

## 6. Risk engine (`risk.py` — pure, no I/O)

### 6.1 Interface

```python
def evaluate(scores_and_flags: list[NodeOutcome], missed_calls_before: int) -> RiskResult
# RiskResult = { level: 'green'|'yellow'|'red', score: int, reasons: list[str] }
# NodeOutcome = { node_id, digit, score, reason: str|None, forced_red: bool }
```

### 6.2 Rules

- `forced_red` (any) ⇒ **red**, score irrelevant.
- `score >= 6` ⇒ red · `2..5` ⇒ yellow · `0..1` ⇒ green.
- `missed_calls_before >= 2` (unreachable family) ⇒ minimum **yellow**, reason
  `"family unreachable on 2 scheduled calls"` (J3).
- Reasons order: red-flag reasons first, then adherence, then informational.

### 6.3 Post-call effects (engine caller, `ivr/engine.py`)

- green/yellow/red → `followup_calls.risk_level`, escalate iff red (dedup: skip if an
  open escalation already exists for the enrollment).
- red ⇒ create escalation (status `open`) + `notify.telegram_red()` + SSE `escalation`.

### 6.4 Required unit tests (all must pass)

1. All zeros ⇒ green, score 0.
2. Single yellow answer (score 2) ⇒ yellow.
3. `forced_red` with total score 0 ⇒ red.
4. Scores 2+2+2 ⇒ red (≥6).
5. Two prior missed calls + all zeros ⇒ yellow with unreachable reason.
6. Reasons list preserves order & content.
7. Score exactly 5 ⇒ yellow; exactly 6 ⇒ red (boundary).
8. Empty outcomes ⇒ green.

## 7. Stewardship aggregation (`GET /api/amr/summary`)

Compute with plain SQLAlchemy over `followup_calls`, `call_responses`, `escalations`:

| KPI | Definition |
|---|---|
| enrolled | count of enrollments |
| reach_rate | completed calls / (completed + no_answer + failed) |
| course_completion_rate | `q_course_done` digit '1' responses / all `q_course_done` responses |
| self_med_rate | enrollments with any `q_self_med`='2' / enrollments asked |
| median_ack_minutes | median(acked_at − created_at) over acked escalations |
| call_minutes / est_cost_inr | sum(duration_sec)/60; × ₹1.0 (constant, note in UI) |
| adherence_buckets | distribution of `q_pillcount` digits |

## 8. Kannada script deck (`app/audio/scripts_kn.json`)

Format: `{ "<clip_id>": {"kn": "...", "en": "..."}, ... }`. `en` is for the demo
console transcript + reviewer. **Every clip must be validated by the native-Kannada
reviewer before demo day; regenerate with `gen_audio.py --force` after edits.**
TTS note: keep sentences short; avoid English loanwords where a common Kannada word
exists ("ಔಷಧಿ" for medicine; "ಗೋಳಿ" for tablet; "ಗಾಯ" for wound).

| clip_id | English meaning | Kannada text (VALIDATE) |
|---|---|---|
| `greet` | Greetings. This is a health check-up call from [hospital], after the patient's discharge. | ನಮಸ್ಕಾರ. ಇದು ಆಸ್ಪತ್ರೆಯಿಂದ, ರೋಗಿ ಡಿಸ್ಚಾರ್ಜ್ ಆದ ನಂತರದ ಆರೋಗ್ಯ ಪರಿಶೀಲನೆ ಕರೆ. |
| `confirm_family` | Are you the patient's family member? Yes: press 1. No: press 2. | ನೀವು ರೋಗಿಯ ಕುಟುಂಬದವರೇ? ಹೌದು ಎಂದರೆ ೧ ಒತ್ತಿರಿ. ಇಲ್ಲ ಎಂದರೆ ೨ ಒತ್ತಿರಿ. |
| `wrong_person` | Sorry for the disturbance. Thank you. | ತಪ್ಪಾದ ಕರೆಗೆ ಕ್ಷಮಿಸಿ. ಧನ್ಯವಾದಗಳು. |
| `q_wound` | How is the wound? Healing well: 1. Some pain or swelling: 2. Pus, bleeding, or fever: 3. | ಗಾಯ ಹೇಗಿದೆ? ಚೆನ್ನಾಗಿ ಗುಣಮುಖವಾಗುತ್ತಿದ್ದರೆ ೧ ಒತ್ತಿರಿ. ಸ್ವಲ್ಪ ನೋವು ಅಥವಾ ಊತ ಇದ್ದರೆ ೨ ಒತ್ತಿರಿ. ಹುಳು, ರಕ್ತಸ್ರಾವ, ಅಥವಾ ಜ್ವರ ಇದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_fever` | How is the fever today? No fever: 1. Mild fever: 2. High fever with chills: 3. | ಇಂದು ಜ್ವರ ಹೇಗಿದೆ? ಜ್ವರ ಇಲ್ಲದಿದ್ದರೆ ೧ ಒತ್ತಿರಿ. ಸ್ವಲ್ಪ ಜ್ವರ ಇದ್ದರೆ ೨ ಒತ್ತಿರಿ. ಹೆಚ್ಚು ಜ್ವರ ಮತ್ತು ನಡುಕ ಇದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_breath` | Any difficulty in breathing? No: 1. Yes: 2. | ಉಸಿರಾಟದಲ್ಲಿ ತೊಂದರೆ ಇದೆಯಾ? ಇಲ್ಲ ಎಂದರೆ ೧ ಒತ್ತಿರಿ. ಹೌದು ಎಂದರೆ ೨ ಒತ್ತಿರಿ. |
| `q_symptom_course` | Compared to before, is the illness better, same, or worse? Better: 1. Same: 2. Worse: 3. | ಕಾಯಿಲೆ ಈಗ ಹೇಗಿದೆ? ಕಡಿಮೆಯಾಗಿದ್ದರೆ ೧, ಹೀಗೇ ಇದ್ದರೆ ೨, ಹೆಚ್ಚಾಗಿದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_meds_today` | Did the patient take all of today's medicines? All: 1. Some missed: 2. Stopped completely: 3. | ಇಂದಿನ ಎಲ್ಲಾ ಔಷಧಿಗಳನ್ನು ರೋಗಿ ತಗೊಂಡಿದ್ದಾರೆ? ಎಲ್ಲಾ ತಗೊಂಡಿದ್ದರೆ ೧, ಕೆಲವು ಮಿಸ್ ಆಗಿದ್ದರೆ ೨, ಪೂರ್ತಿ ನಿಲ್ಲಿಸಿದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_pillcount` | Open the tablet strip. How many tablets are left? Up to 3: press 1. 4 to 7: press 2. More than 7: press 3. | ಗೋಳಿಯ ಪಟ್ಟಿಯನ್ನು ತೆರೆಯಿರಿ. ಇನ್ನೂ ಎಷ್ಟು ಗೋಳಿಗಳು ಉಳಿದಿವೆ? ಮೂರರೊಳಗೆ ಇದ್ದರೆ ೧, ನಾಲ್ಕರಿಂದ ಏಳು ಇದ್ದರೆ ೨, ಏಳಕ್ಕಿಂತ ಹೆಚ್ಚು ಇದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_course_done` | Has the full medicine course told by the doctor finished? Yes: 1. Still remaining: 2. Stopped midway: 3. | ವೈದ್ಯರು ಹೇಳಿದ ಪೂರ್ತಿ ಔಷಧಿ ಕೋರ್ಸ್ ಮುಗಿದಿದೆಯಾ? ಹೌದು ಎಂದರೆ ೧, ಇನ್ನೂ ಉಳಿದಿದ್ದರೆ ೨, ಮಧ್ಯದಲ್ಲೇ ನಿಲ್ಲಿಸಿದ್ದರೆ ೩ ಒತ್ತಿರಿ. |
| `q_self_med` | Is the patient taking any medicine the doctor did NOT prescribe? No: 1. Yes: 2. | ವೈದ್ಯರು ಸೂಚಿಸದ ಬೇರೆ ಯಾವುದಾದರೂ ಔಷಧಿ ತಗೊಳ್ತಾ ಇದ್ದೀರಾ? ಇಲ್ಲ ಎಂದರೆ ೧, ಹೌದು ಎಂದರೆ ೨ ಒತ್ತಿರಿ. |
| `q_leftover` | Are any old antibiotic tablets left at home? No: 1. Yes: 2. | ಹಿಂದಿನ ಯಾಂಟಿಬಯಾಟಿಕ್ ಗೋಳಿಗಳು ಮನೆಯಲ್ಲಿ ಉಳಿದಿವೆಯಾ? ಇಲ್ಲ ಎಂದರೆ ೧, ಹೌದು ಎಂದರೆ ೨ ಒತ್ತಿರಿ. |
| `counsel_yellow` | There is some concern. We will call again tomorrow. If it worsens, come to the hospital immediately. | ಸ್ವಲ್ಪ ತೊಂದರೆ ಕಂಡಿದೆ. ನಾಳೆ ಮತ್ತೆ ಕರೆ ಮಾಡುತ್ತೇವೆ. ತೊಂದರೆ ಹೆಚ್ಚಾದರೆ ತಕ್ಷಣ ಆಸ್ಪತ್ರೆಗೆ ಬನ್ನಿ. |
| `counsel_adherence` | Please give the medicines on time until the full course finishes. Stopping midway can bring the illness back stronger. | ದಯವಿಟ್ಟು ಔಷಧಿಯನ್ನು ಸಮಯಕ್ಕೆ, ಪೂರ್ತಿ ಕೋರ್ಸ್ ಮುಗಿಯುವವರೆಗೆ ಕೊಡಿ. ಮಧ್ಯದಲ್ಲಿ ನಿಲ್ಲಿಸಿದರೆ ಕಾಯಿಲೆ ಮತ್ತೆ ಹೆಚ್ಚಾಗಬಹುದು. |
| `counsel_selfmed` | Please do not take medicines that the doctor has not prescribed. It can be harmful. | ವೈದ್ಯರು ಸೂಚಿಸದ ಔಷಧಿಗಳನ್ನು ತಗೊಳ್ಳಬೇಡಿ. ಅದು ಹಾನಿಕಾರಕವಾಗಬಹುದು. |
| `counsel_leftover` | Do not share or reuse leftover antibiotic tablets. Please return them to the hospital or medicine shop. | ಉಳಿದ ಯಾಂಟಿಬಯಾಟಿಕ್ ಗೋಳಿಗಳನ್ನು ಯಾರಿಗೂ ಕೊಡಬೇಡಿ, ಮತ್ತೆ ಬಳಸಬೇಡಿ. ಅವುಗಳನ್ನು ಆಸ್ಪತ್ರೆ ಅಥವಾ ಔಷಧಾಲಯಕ್ಕೆ ಹಿಂತಿರುಗಿಸಿ. |
| `red_response` | This is a serious sign. Please go to the nearest hospital immediately. For emergency help call 104 or 108. | ಇದು ಗಂಭೀರ ಲಕ್ಷಣ. ದಯವಿಟ್ಟು ತಕ್ಷಣ ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆಗೆ ಹೋಗಿ. ತುರ್ತು ಸಹಾಯಕ್ಕೆ ೧೦೪ ಅಥವಾ ೧೦೮ ಗೆ ಕರೆ ಮಾಡಿ. |
| `edu_amr` | Important: always complete the full antibiotic course. Never share antibiotics. Never keep them for next time. | ಮುಖ್ಯ ಸೂಚನೆ: ಯಾಂಟಿಬಯಾಟಿಕ್ ಕೋರ್ಸ್ ಯಾವಾಗಲೂ ಪೂರ್ತಿ ಮಾಡಿ. ಔಷಧಿಯನ್ನು ಹಂಚಿಕೊಳ್ಳಬೇಡಿ, ಮುಂದಕ್ಕೆ ಇಡಬೇಡಿ. |
| `edu_viral` | Important: antibiotics do not work on cold and viral fever. Never take them without the doctor's advice. | ಮುಖ್ಯ ಸೂಚನೆ: ಶೀತ ಮತ್ತು ಸೋಂಕಿನ ಜ್ವರಕ್ಕೆ ಯಾಂಟಿಬಯಾಟಿಕ್ ಕೆಲಸ ಮಾಡುವುದಿಲ್ಲ. ವೈದ್ಯರ ಸಲಹೆಯಿಲ್ಲದೆ ತಗೊಳ್ಳಬೇಡಿ. |
| `closing` | Thank you. We wish the patient a quick recovery. For emergencies call 104. | ಧನ್ಯವಾದಗಳು. ರೋಗಿ ಬೇಗ ಗುಣಮುಖರಾಗಲಿ. ತುರ್ತು ಇದ್ದರೆ ೧೦೪ ಗೆ ಕರೆ ಮಾಡಿ. |
| `verify_call` | (desk verification) This call is to confirm your phone number for hospital follow-up. Please press 1. | ಆಸ್ಪತ್ರೆ ಮರಳಿ ಪರಿಶೀಲನೆಗಾಗಿ ನಿಮ್ಮ ಫೋನ್ ಸಂಖ್ಯೆಯನ್ನು ಖಚಿತಪಡಿಸಲು ಈ ಕರೆ. ದಯವಿಟ್ಟು ೧ ಒತ್ತಿರಿ. |
| `timeout_reprompt` | We did not hear your answer. Please press your answer now. | ನಿಮ್ಮ ಉತ್ತರ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಈಗ ಒತ್ತಿರಿ. |

## 9. FHIR export (`fhir.py`) — ABDM-ready artifact

`GET /api/patients/{id}/fhir` returns a FHIR R4 **Bundle (type=document)** for the
latest enrollment, aligned to the ABDM/NRCeS `DischargeSummaryRecord` profile:

- `Composition` (type code `373942005` "Discharge summary" SNOMED-CT; sections:
  diagnoses, medications, follow-up instructions) — profile URL
  `https://nrces.in/ndhm/fhir/r4/StructureDefinition/DischargeSummaryRecord`
- `Patient` (name, age/gender, identifier = ABHA number if present)
- `Encounter` (discharge date, hospital name as `serviceProvider` display)
- `Condition` (condition_label as text)
- `MedicationRequest` per `enrollment_meds` row (med name, course duration)

Pragmatic scope: correct structure + codes; do not attempt signature/encryption or
HIE-CM exchange. Comment in code: *"validate against NRCeS IG before production
submission."*

## 10. LLM assist (Groq `llama-3.3-70b-versatile`) — optional, edge-only, fallback-safe

The IVR/call path **never** touches an LLM. Exactly two features use Groq, only when
`GROQ_API_KEY` is set, via OpenAI-compatible REST with the pinned `httpx` (no SDK).
Design rule: **the LLM assembles and selects — it never invents clinical content.**

### 10.1 Feature A — intake protocol suggestion

Nurse types a condition label (e.g. "appendectomy post-op, drain removed day 2") →
`POST /api/enrollments/suggest` → LLM picks the best protocol and drafts English
sheet instructions.

- Request to Groq: `POST https://api.groq.com/openai/v1/chat/completions`,
  `Authorization: Bearer $GROQ_API_KEY`, body:
  `{model: $LLM_MODEL, temperature: 0.2, max_tokens: 400, response_format: {"type":"json_object"},
   messages: [system, user]}`.
- System prompt (verbatim):
  > You are a discharge-triage assistant for a Karnataka government hospital. Given a
  > condition label, choose exactly one follow-up protocol id from this list:
  > {protocol_ids_with_descriptions}. Also draft 3-5 short English home-care
  > instruction sentences appropriate to the condition. Rules: no drug names, no
  > dosages, no diagnosis claims, no emergency advice beyond 'go to hospital if
  > worsening'. Reply ONLY as JSON: {"protocol_id": "...", "instructions_en": ["..."],
  > "note": "one short line for the nurse"}.
- Server-side validation (all must pass, else return template fallback with 200):
  `protocol_id` ∈ loaded protocols; instructions are ≤6 strings, each ≤120 chars,
  stripped of anything matching `mg|ml|dose|tablet\s+\d` (dosage guard).
- When `GROQ_API_KEY` unset → `503 {"detail":"llm disabled"}`; the UI hides the
  `[ ✦ suggest ]` button entirely. Enrollment works identically without it.

### 10.2 Feature B — personalized caregiver sheet

On enrollment creation, the LLM **selects and orders** bullets for the Kannada sheet:

- Input: protocol's pre-approved `sheet.bullets_kn` bank (indexed list) + condition
  label + Feature-A instructions (if any).
- LLM returns `{"bullet_indices": [int,...], "heading_en": "..."}`.
- **Kannada text is rendered only from the pre-approved bank, by index.** The LLM can
  choose order/subset — it can never write new Kannada clinical sentences (mistranslated
  medical advice is a patient-safety issue, and this design makes it impossible).
- Result stored on the enrollment as `sheet_instructions` JSON
  `{"bullets_kn": [...resolved strings...], "instructions_en": [...], "source": "llm"|"template"}`.
- Fallback: `source:"template"` = first 5 bullets in bank order. Any LLM error, timeout,
  invalid JSON, out-of-range index, or unset key → template fallback, silently.

### 10.3 Failure/timeout contract (test all four in `test_llm.py`)

| Condition | Behavior |
|---|---|
| key unset | suggest → 503; sheets → template |
| HTTP error / timeout (4s) | one retry (2s), then template/503 |
| invalid JSON / schema | template/503 |
| valid but dosage-guard trip / bad index | template/503 |

No LLM call may block enrollment: Feature B runs **after** the enrollment row commits,
updates `sheet_instructions` in place, and the enrollment response returns immediately
with the template version if the LLM is slow.
