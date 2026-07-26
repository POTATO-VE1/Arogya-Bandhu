# 01 — Product

## 1. Elevator pitch

> India's digital health systems (eSanjeevani, ABDM) go blind the moment a patient
> walks out of a government hospital. The 7–14 days after discharge are the
> highest-risk window with zero monitoring. Aarogya Bandhu is the discharge-to-recovery
> layer: a nurse enrolls the patient in under a minute, the family gets Kannada voice
> follow-up calls on any basic feature phone, and the hospital sees red flags the same
> day instead of at readmission. The same calls run an antibiotic-misuse track — pill
> counts, course completion, self-medication screening — giving the hospital its first
> stewardship signal without hiring anyone.

## 2. Problem (sharp)

- Discharged patients (esp. post-op, postpartum, infection-on-antibiotics) leave with
  a prescription and verbal instructions — frequently to families with low literacy
  and no smartphone.
- Surgical site infections, missed doses, and incomplete antibiotic courses surface
  only when the patient returns — late, sicker, costlier.
- Antibiotic misuse (incomplete courses, sharing leftovers, self-medication) is a
  national AMR driver (India NAP-AMR; WHO AWaRe). Hospitals have **no data** on what
  patients do at home.
- Existing systems (eSanjeevani, ABDM, HMIS) record the episode; none of them watch
  the recovery.

## 3. Actors & who does what (J1)

| Actor | Device | What they do |
|---|---|---|
| **Nurse / ward staff** | Low-end Android (Chrome, PWA) | Enrolls patient at discharge desk (<60s), verifies caregiver number live, works the escalation queue |
| **Doctor / MO** | Any | Views board & AMR stats. **No data entry in default flow.** |
| **Caregiver (family)** | Feature phone, Kannada | Answers IVR calls, presses digits, hears counseling |
| **System** | — | Schedules calls, runs IVR, scores risk, escalates, aggregates AMR stats |
| **On-call staff** | Any phone | Gets Telegram red-flag alert with one-tap callback link |

## 4. Core loop

```
DISCHARGE DESK                      SYSTEM                         HOME (Day 1/3/7/14)
───────────────                     ──────                         ───────────────────
nurse: tiles + meds           →     protocol JSON (Kannada)   →    IVR call, Kannada
caregiver number + consent          schedules calls                DTMF answers
[Verify now] instant test call      risk engine scoring              ↓
Kannada print sheet hands over      escalation if RED           ←  counseling clip / 104-108 advice
                                          ↓
                                    dashboard flips + Telegram ping → nurse acks → audit log
```

**Outcomes (KPIs, J10):** enrolled count · reach rate · course-completion rate ·
self-medication rate · median red-flag ack time. All computed live (`/api/amr/summary`
+ board header).

## 5. Conditions shipped (J4)

| Protocol | Who | Key red flags | AMR hooks |
|---|---|---|---|
| `wound_care` | Post-op surgical | pus/bleeding/fever | adherence today, pill count, edu byte |
| `antibiotic_course` | Infection discharged on antibiotics | worsening fever/symptoms | pill count vs expected, course completion, leftover screening, self-med |
| `fever_viral` | Fever/viral syndrome | high fever + chills/rigors, breathlessness | "no antibiotics for viral" edu, self-med screening |

## 6. Positioning vs existing systems (J6)

- **ASHA workers / nurses:** we produce their daily priority list; we don't replace them.
- **104 Arogya Vani / 108:** our escalation scripts route emergencies *to* them.
- **ABDM:** optional ABHA number captured; per-patient FHIR R4 `DischargeSummaryRecord`
  export ("ABDM-ready"); live exchange is roadmap.
- **eSanjeevani:** narrative — red-flag patients could be routed into teleconsult slots.
- **NTEP/Nikshay:** roadmap — TB adherence is a natural 4th protocol.

## 7. Compliance posture (J2, J7, J8)

- **Consent:** verbal consent at desk, noted on print sheet, checkbox + timestamp stored.
- **Voice, not SMS:** voice calls need **no DLT registration** (SMS does — that's why
  we don't send any). Calls placed 09:00–21:00 only. Consented service calls to our
  own patients; production path = Indian CPaaS with registered headers (TRAI 1600-series).
- **DPDP-aware:** data minimization, no Aadhaar, no call recording (DTMF digits only),
  anonymized aggregates, retention note (delete pilot data on request).
- **Liability:** triage layer, not an emergency service. Stated on UI footer, print
  sheet, and call closing. Escalations have ack SLA + audit trail.

## 8. Unit economics (J9)

~4 calls × ~2 min × ~₹1/min (Indian CPaaS) ≈ **₹8–12 per patient episode.**
Compare: one SSI readmission workup, one extra OPD revisit, one nurse-hour of manual
follow-up calls. Dashboard shows live call-minute counters so the number is always
on screen.

## 9. Scope

**MUST (demo-critical):** login · enrollment + number verify · 3 protocols · scheduler ·
Twilio IVR loop · risk engine · board with live risk flip · escalation queue + ack ·
Telegram alert · Kannada audio + print sheet · AMR summary · Demo Call Console · seed.

**NICE (if ahead):** FHIR export *(committed)* · Groq LLM assist — intake protocol
suggestion + sheet personalization, flag-gated, template fallback (docs/03 §10) ·
repeat-option (press 9).

**CUT (never for this event):** SMS (see §7 — blocked by DLT, unreliable delivery, wrong
channel for low-literacy users; the `notify.py` seam accepts a future SMS adapter
post-DLT without touching call code) · WhatsApp · family-side app/Telegram · ASR/voice
recognition · LLM in the call path · multi-hospital admin UI · Exotel adapter · ABHA
creation flow.

## 10. Roadmap (pitch only — do not build)

ABDM live linkage (HFR/HIP) · eSanjeevani slot routing · ASHA mobile worklist · TB
(Nikshay-aligned) protocol · consent-first wearable/device integrations with explicit
opt-in + on-device anonymization (acknowledge misuse/breach concerns head-on) ·
dialect tuning (N. Karnataka vs Mysuru Kannada).
