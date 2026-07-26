# 00 — Brutal Judge Feedback (and the design answers baked into the spec)

Persona: **Dr. S.** — senior surgeon at a Karnataka district hospital, 20 years in
government service, informally the hospital's "tech person", has judged hackathons
before, has seen 50 pitch decks about "AI for rural health" die on contact with a real
OPD. He is sympathetic to the idea and therefore ruthless about the details.

Each attack below has an ID (J1–J10). The required design answer is **already baked
into the spec** — the "Where addressed" column tells the builder where. Do not regress
on these; they are the difference between a trophy and a "nice try".

---

## J1. "My doctors will not touch your app. Not even for 60 seconds."

> A district OPD doctor sees 100–200 patients a day, ~2 minutes each. Any workflow that
> adds data entry to the *doctor* is dead on arrival. Discharge counseling is done by
> nurses and ward staff — if your system needs a doctor at a keyboard, you have built
> for a private hospital fantasy.

**Answer baked in:** Enrollment is **nurse-owned** at the discharge desk. The doctor's
involvement is zero in the default flow (protocols are pre-built templates). The nurse
picks condition tiles, confirms meds, enters the caregiver number. Consent text is
printed on the sheet. *(docs/01 §3, docs/05 §6.2)*

## J2. "Who is liable when your system flags red at 11 PM and nobody acts?"

> If a red flag sits in a queue overnight and the patient deteriorates, that is on the
> hospital — and you just gave them a new way to fail, with a paper trail. What is the
> SLA? What does the system promise, exactly?

**Answer baked in:** The product is explicitly a **triage layer, not an emergency
service** — said in the UI footer, on the printed sheet, and in the closing clip of
every call, which directs emergencies to **104 (Arogya Vani) / 108 (ambulance)**.
Escalations carry an **acknowledgement SLA (2h)** with visible ack state and an
immutable `audit_log`. We never promise response; we promise visibility + a queue.
*(docs/02 §5 escalations/audit, docs/03 script deck `red_response`, docs/05 §6.6)*

## J3. "Half the phone numbers we collect are wrong, shared, or switched off."

> Caregiver numbers are recited from memory, belong to a neighbor, or die with a
> ₹500 phone battery. And illiterate caregivers press random keys. Your beautiful data
> pipeline starts with garbage.

**Answer baked in:** **Number verification at the desk**: the intake form has a
"Verify now" button that places an instant test call *while the family is still
standing there* — if nobody answers and presses 1, the enrollment is flagged.
Unreachable handling: missed call → retry after 2h → 2 misses = `unreachable` yellow
flag, which is itself surfaced as a KPI ("we cannot reach 22% of families" is
operationally valuable information, not a failure we hide). *(docs/02 §5,
docs/04 §5, docs/05 §6.2)*

## J4. "A generic 'how are you feeling' call is clinically worthless."

> Post-discharge risk is concentrated in specific cohorts: surgical site infections,
> postpartum, fever/infection on antibiotics. Each has different red flags, different
> timelines, different questions. Generic = toy.

**Answer baked in:** Condition-specific protocol JSONs with clinically sensible red
flags (wound: pus/bleeding/fever = red; fever: high fever + chills/rigors = red), and
the architecture makes a protocol a **content file, not code** — a medical officer can
author a new one without a developer. Three protocols ship; the format is the product.
*(docs/03 §2–§4)*

## J5. "Asking 'did you finish your antibiotics' is theater. Everyone says yes."

> Self-reported adherence has massive courtesy bias. Patients tell the nice voice what
> it wants to hear. If your AMR claim rests on that question, it is hollow.

**Answer baked in:** **Pill counts**, not self-report: *"Open the strip. How many
tablets are left? 0–3 press 1, 4–7 press 2, 8+ press 3"* — countable, cross-checkable
against expected remaining doses for that day of the course, and the mismatch becomes
the adherence signal. Plus course-completion timing (the completion question only asks
on/after the expected end date) and leftover-medication screening.
*(docs/03 §5, §6) — this is the headline AMR mechanism; demo it explicitly.*

## J6. "Karnataka already has ASHA workers, 104, Nikshay. Why are you not using them?"

> One ASHA per ~1,000 population, already overloaded. They can't chase every discharged
> surgical patient — but they *can* act on a short priority list. Systems that ignore
> the existing workforce get piloted and die; systems that feed it get adopted.

**Answer baked in:** Positioning: **we do not replace ASHAs/nurses — we produce their
priority list.** The escalation queue is literally designed as the daily worklist for
whoever follows up (nurse today, ASHA in a block-level rollout). Roadmap slide names
NTEP/Nikshay alignment for TB adherence as a future protocol. *(docs/01 §6, docs/07)*

## J7. "You are collecting health data. DPDP Act. Consent. Data residency. Speak."

**Answer baked in:** Consent captured at enrollment (verbal consent note on sheet +
checkbox, timestamped). Data minimization: name, age, condition, caregiver phone — no
Aadhaar, no diagnoses free-text. We **store DTMF digits only — calls are never
recorded**. Aggregates on the AMR page are anonymized. UUID ids, hospital scoping,
audit log, retention note. Twilio data-flow is acknowledged honestly in the pitch with
the production path (Indian CPaaS, e.g. Exotel). *(docs/02 §7, docs/01 §7, docs/07)*

## J8. "TRAI is strangling robocalls. How does this not get blocked as spam?"

**Answer baked in:** These are **consented service calls** to our own discharged
patients, placed 09:00–21:00, from a registered sender in production (Indian CPaaS;
TRAI's 1600-series for transactional voice). Voice requires **no DLT** (that's SMS-only)
— stated confidently in the pitch. Demo uses Twilio trial (international caller ID)
and we say exactly that on the roadmap slide. *(docs/01 §7, docs/07)*

## J9. "What does one patient cost you? 'Free trial' is not a business model."

**Answer baked in:** Unit economics slide: ~4 calls/patient × ~2 min × ~₹1/min ≈
**₹8–12 per patient episode** at Indian CPaaS rates, vs. the cost of one readmission
or one SSI workup. Dashboard tracks live cost counters. Judges get the number, not a
shrug. *(docs/01 §5, docs/02 §5 `followup_calls`, docs/07)*

## J10. "Show me outcomes, not screens."

**Answer baked in:** Five KPIs computed live and shown on the AMR/stats page:
**enrolled, reach rate, course-completion rate, self-medication rate, median red-flag
ack time.** The demo ends on these numbers. *(docs/01 §4, docs/03 §7, docs/05 §6.7)*

---

### Summary table

| ID | Attack | Core answer | Spec |
|----|--------|-------------|------|
| J1 | Doctors won't do intake | Nurse-owned enrollment, template protocols | 01, 05 |
| J2 | Liability for missed red flags | Triage-not-emergency framing, ack SLA, audit log | 02, 03, 05 |
| J3 | Wrong/dead numbers, bad DTMF data | Desk-time number verification, unreachable KPI | 02, 04, 05 |
| J4 | Generic protocol is worthless | Condition-specific protocol JSON as content | 03 |
| J5 | Self-reported adherence is gamed | Pill-count verification vs expected doses | 03 |
| J6 | Ignores ASHA/existing workforce | We output the priority list for existing staff | 01, 07 |
| J7 | Privacy / DPDP | Consent, minimization, no recordings, scoping | 01, 02 |
| J8 | TRAI spam crackdown | Consented service calls, 1600-series narrative | 01, 07 |
| J9 | Unit economics | ~₹8–12/patient episode, live cost counters | 01, 07 |
| J10 | No outcomes | 5 live KPIs, demo ends on numbers | 01, 03, 05 |
