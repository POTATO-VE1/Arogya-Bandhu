# 07 — Demo Script, Fallbacks, Pitch, Q&A

## 1. The 5-minute demo arc (rehearse exactly this)

| Time | Action | Line |
|---|---|---|
| 0:00–0:30 | Problem slide: the 7–14 day blind window | "eSanjeevani treated the patient. ABDM filed the record. The moment she walks out of the district hospital, both go blind." |
| 0:30–1:15 | **Intake** on phone: nurse enrolls "Lakshmamma", wound_care + Amoxiclav `[Watch]`, caregiver number, consent, verify-number call fires | "Sixty seconds, by the nurse, at the discharge desk. The doctor never touches it." |
| 1:15–1:45 | Show Kannada sheet (screen), mention print | "The family leaves with this. No app, no smartphone, no literacy needed." |
| 1:45–3:00 | **The moment:** `trigger call` → a real phone rings → Kannada IVR on speaker → volunteer presses **3** (pus/fever) → board flips `[RED]` live → Telegram pings | "Press 3… watch the board. The hospital just saw a patient it was blind to five minutes ago." |
| 3:00–3:45 | Escalation page: ack it, show `acked · 42m` + audit | "Every red flag has an owner and a clock. It's a triage layer — emergencies are always routed to 104/108." |
| 3:45–4:30 | **AMR page:** pill-count story, completion %, self-med % | "We don't ask 'did you finish your antibiotics' — everyone lies. We ask them to count the strip. That is how you fight antimicrobial resistance without hiring anyone." |
| 4:30–5:00 | KPIs + unit economics + roadmap (ABDM FHIR, eSanjeevani routing, ASHA worklists) | "₹8–12 per patient. Five live outcome metrics. And it already exports ABDM-ready FHIR." |

## 2. Fallback ladder (test each the day before)

1. **Primary:** Twilio live call to a pre-verified phone (speaker on).
2. **Network degraded:** Demo Call Console (`/demo`) — same engine, same audio, in-browser.
3. **Total connectivity loss:** seeded board + pre-recorded 90s screen capture + local
   audio playback of the call script.
4. **Twilio trial minute exhaustion:** console + narration; keep ₹1,700 reserve to
   upgrade mid-event if needed (docs/01 §8).
5. **Tunnel flaps mid-demo:** `cloudflared` restart command printed in RUNBOOK; board
   still works on localhost — narrate through it.

## 3. Pitch deck (10 slides, one idea each)

1. The blind window (7–14 days post-discharge).
2. Who pays for it: SSI readmissions, AMR (India NAP-AMR, Red Line campaign).
3. The product in one loop diagram (docs/01 §4).
4. Why feature-phone IVR wins (no smartphone/literacy/internet; DTMF; voice needs no DLT).
5. The AMR mechanism: pill counts, not promises (J5).
6. Live demo.
7. Outcomes dashboard: the 5 KPIs (J10).
8. Unit economics: ₹8–12/patient vs readmission cost (J9).
9. Built on India's DPI: Bhashini, ABDM-ready FHIR; complements ASHA/104/108/eSanjeevani (J6).
10. Roadmap + ask: pilot in one district hospital (Victoria/Bowring/KIMS-class), one ward, 4 weeks.

## 4. Judge Q&A prep (30-second answers — map to docs/00)

- **"Doctors won't use it"** → J1: nurse-owned, template protocols, doctor read-only.
- **"Liability at 2 AM?"** → J2: triage layer, every call closes with 104/108, ack SLA + audit trail; we make existing duty-of-care *visible*, we don't replace it.
- **"Wrong numbers / illiterate families?"** → J3: desk-time number verification call; unreachable rate is a surfaced KPI, not a hidden failure.
- **"Self-reported adherence is garbage"** → J5: pill-count buckets cross-checked against expected remaining doses.
- **"ASHA/Nikshay exist"** → J6: we output their daily priority list; TB protocol is a natural Nikshay-aligned extension.
- **"Privacy / DPDP?"** → J7: consent at desk, minimization, no call recordings (digits only), anonymized aggregates, hospital-scoped data.
- **"TRAI will block you"** → J8: consented service calls to own patients, 9–21 IST, voice is DLT-exempt; production = Indian CPaaS with 1600-series headers.
- **"Cost?"** → J9: ₹8–12/episode; live counter on the AMR page.
- **"Why not WhatsApp/app?"** → the patient-side thesis: no smartphone, no literacy, no internet. Staff side is already a web app.
- **"Kannada dialects?"** → short standard-Kannada sentences validated by a native speaker; dialect packs are a content change, not code.
- **"Wearables?"** → roadmap only, consent-first; acknowledge misuse risk explicitly — opt-in, minimization, on-device aggregation. (Shows maturity, addresses the concern unprompted.)
- **"Where's the AI in this?"** → deliberately at the edges: Groq 70B assists the nurse (protocol suggestion) and personalizes the Kannada sheet by *selecting from pre-approved clinical text* — while the patient-facing call flow is 100% deterministic templates, because a hallucination on a feature phone is a patient-safety incident, and a rule-based risk engine is auditable by a medical officer. Reliability on a 2G ward beats buzzwords.
- **"Why no SMS reminders?"** → three reasons: TRAI DLT registration takes days and unregistered SMS is blocked at the operator; international-originated A2P SMS to India is heavily filtered, so even a demo flakes; and our caregivers read less than they listen — voice is the inclusive channel. The notification layer is already shaped so an SMS adapter slots in post-DLT.

## 5. Pre-demo checklist

- [ ] Twilio: demo phone + backup phone verified; ≥60 trial minutes left
- [ ] Tunnel stable 30+ min; `PUBLIC_BASE_URL` current
- [ ] `seed_demo.py --reset` run; board looks lived-in
- [ ] Kannada clips regenerated post-review (`gen_audio.py --force`)
- [ ] Telegram group: judge-visible phone logged in, notifications on
- [ ] Fallback video on local disk; Demo Console tested twice
- [ ] Laptop + demo phone charged; hotspot ready as backup uplink
