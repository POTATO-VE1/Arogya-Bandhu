# 05 — UI Design System (derived from opencode.ai)

The visual language is extracted from the actual OpenCode website stylesheet — not an
imitation. Tokens below are the real values. The result: a professional, terminal-grade
dark UI that looks like a clinical instrument, not a startup landing page.

## 1. Design principles

1. **Everything is monospace.** OpenCode sets `--font-sans: var(--font-mono)` — we do
   the same. One typeface: **IBM Plex Mono** (bundled via `@fontsource/ibm-plex-mono`,
   works offline). Kannada text uses **Noto Sans Kannada** (`@fontsource/noto-sans-kannada`).
2. **Structure over decoration.** 1px borders, flat surfaces, zero shadows, zero
   gradients, zero images (except the logo block).
3. **Text is the interface.** Status is `[RED]` not a colored dot; actions are
   `[ trigger call ]` not icon buttons; markers are `[*]` like OpenCode's feature list.
4. **Instant.** No animations except ≤120ms opacity fades and one blinking block cursor.
5. **Touch-real.** Staff use low-end Android: min 44px targets, generous row height,
   no hover-dependent behavior.

## 2. Tokens (`src/theme.css` — implement exactly)

```css
:root {
  /* surfaces (opencode.ai dark) */
  --color-bg: #0c0c0e;
  --color-bg-surface: #161618;
  --color-bg-elevated: #1c1c1f;
  --color-border: #38383a;
  --color-border-muted: #2c2c2e;

  /* text */
  --color-text: #ffffff;
  --color-text-secondary: #c7c7cc;
  --color-text-muted: #a1a1a6;
  --color-text-disabled: #86868b;

  /* semantic */
  --color-accent: #007aff;
  --color-accent-hover: #0056b3;
  --color-danger: #ff453a;
  --color-warning: #ff9f0a;
  --color-success: #30d158;

  /* type */
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --font-kn: "Noto Sans Kannada", sans-serif;
  --font-sans: var(--font-mono);          /* the opencode move: mono everywhere */
  --font-size-xs: 0.75rem;    /* 12 — micro labels, uppercase */
  --font-size-sm: 0.8125rem;  /* 13 — secondary text, table cells */
  --font-size-md: 0.9375rem;  /* 15 — body */
  --font-size-lg: 1.125rem;   /* 18 — panel titles */
  --font-size-xl: 1.25rem;    /* 20 — page titles, stats (use weight 600) */

  --radius: 0px;              /* panels/tables */
  --radius-interactive: 4px;  /* buttons, inputs, badges only */
  --space-unit: 8px;          /* spacing = multiples of 8 */
}
```

Tailwind v4: map these into `@theme` (colors `bg`, `surface`, `elevated`, `border`,
`border-muted`, `text*`, `accent`, `danger`, `warning`, `success`; font `mono`, `kn`).
Body: `background: var(--color-bg); color: var(--color-text); font-family: var(--font-mono);
font-size: var(--font-size-md);`.

## 3. Layout shell

```
┌──────────────────────────────────────────────────────────────────┐
│ ▚▚ AAROGYA BANDHU      board  intake  escalations  amr  demo      │  ← topbar: 48px,
│    DISTRICT HOSPITAL DEMO                          staff ▾ logout │    border-bottom 1px
├──────────────────────────────────────────────────────────────────┤
│  <page content, max-width 1100px, padding 16px (mobile) / 24px>   │
├──────────────────────────────────────────────────────────────────┤
│ ● connected · 3 calls today · 1 open escalation · triage layer —  │  ← statusbar: always
│   not an emergency service · emergencies → 104 / 108              │    visible (J2)
└──────────────────────────────────────────────────────────────────┘
```

- Logo: two solid 8×8 squares (`▚▚` as inline-block divs, `--color-text` and
  `--color-border`) + wordmark uppercase, letter-spacing 0.08em.
- Nav links: plain text, `text-muted`; active = `text` + underline offset 4px;
  hover = underline (opencode nav behavior).
- Statusbar text: `xs`, `text-muted`; leading `●` is `--color-success` when SSE
  connected, `--color-danger` when not.

## 4. Components (exact specs)

**Panel** — `bg: surface; border: 1px solid border; radius: 0; padding: 16px`.
Optional header: `font-size: xs; text-transform: uppercase; letter-spacing: 0.1em;
color: text-muted; border-bottom: 1px solid border-muted; padding-bottom: 8px`.

**RiskBadge** — text badge, not a pill: `[GRN]` success / `[YEL]` warning / `[RED]`
danger / `[ — ]` text-disabled. `font-size: sm; font-weight: 600`. Brackets are literal
characters.

**Button** — two variants only:
- primary: `bg: accent; color: #fff; radius: 4px; padding: 10px 14px; font: inherit;
  hover: accent-hover`. Label style: `[ save ]`.
- ghost: `transparent; border: 1px solid border; color: text-secondary; radius: 4px;
  hover: color text, border-color text-muted`.

**Input / Select** — `bg: bg; border: 1px solid border; radius: 4px; padding: 10px
12px; color: text; font: inherit; focus: border-color accent; outline: none`.
Label above: `xs` uppercase `text-muted`. Error: border `--color-danger` + error line
`text-danger sm` below.

**Table** — full width; header row: `xs` uppercase `text-muted`, `border-bottom: 1px
solid border`; rows: `border-bottom: 1px solid border-muted`, height ≥48px, cells `sm`;
row hover: `bg: elevated`. No zebra stripes.

**Stat** — value `xl`/600 + label `xs` uppercase `text-muted`. Stats render in a row
of Panels (opencode "stats bar" motif).

**KeyHint** — `[*]` literal marker in `--color-accent` before list items (opencode
feature-list motif).

**LogLine** — one line: `14:02:11` (text-disabled) + message (text-secondary). Used
for call transcripts and activity feeds.

**Cursor** — 8×16px block, `--color-accent`, `animation: blink 1.06s step-end
infinite` (`@keyframes blink { 50% { opacity: 0 } }`). Used next to the login title
and Demo console only.

## 5. Interaction rules

- Transitions: `opacity 120ms` only. No slides, no springs, no toasts (messages are
  inline LogLines or status lines).
- Focus visible: 1px `accent` outline, offset 2px. Everything keyboard-navigable.
- Loading state: `…` appended to the panel header. Empty state: one centered line,
  `text-muted`, e.g. `no escalations — all clear`.
- Errors: inline red LogLine near the action. Never alert().

## 6. Pages

### 6.1 `/login`

```
                ┌────────────────────────────────────┐
                │ ▚▚ AAROGYA BANDHU ▊                │  ← blinking cursor
                │ discharge-to-recovery console      │
                │                                    │
                │ USERNAME                           │
                │ [________________________]         │
                │ PASSWORD                           │
                │ [________________________]         │
                │                                    │
                │ [ sign in ]                        │
                │ 14:02:11 login failed: bad creds   │  ← error LogLine (only on error)
                └────────────────────────────────────┘
```
Centered 380px panel on empty `--color-bg`. Enter submits. On success → `/board`.

### 6.2 `/intake` (nurse-owned, J1 — must be completable in <60s)

Three stacked Panels on one screen (no wizard, no routing away):

1. **PATIENT** — name, age, sex (3 ghost buttons F/M/O), ABHA (optional), ward.
2. **CAREGiver & PROTOCOL** — caregiver name, phone (+91 prefix locked, 10 digits),
   protocol tiles: 3 large ghost buttons (`name_en` + `name_kn` sub-line), condition
   label input, discharge date (default today).
3. **MEDICATIONS** — rows: med select (from catalog; antibiotics show `[Watch]`/
   `[Reserve]` badge in warning color — AMR nudge, docs/03 §5) + course days +
   doses/day; `[ + add med ]`. Paracetamol pre-added, deletable.

Footer row: consent checkbox (`[x] family consented to follow-up calls` — required,
audited) · `[ verify number ]` ghost (fires desk test call; result LogLine:
`number verified ✓` / `no answer`) · `[ enroll ]` primary (disabled until valid).

LLM assist (docs/03 §10): an `[ ✦ suggest ]` ghost button sits next to the condition
label; on success it pre-selects the protocol tile and shows the drafted instructions
as an editable LogLine block. If the backend says `llm disabled`, the button is not
rendered at all — the form is identical without it.

### 6.3 `/board` (home)

Stats row (4 Stats): `enrolled` · `calls today` · `open escalations` (danger when >0) ·
`reach rate`. Then one Panel: today's table.

```
PATIENT        PROTOCOL     DAY  LAST CALL     RISK    ACTIONS
Lakshmamma     wound_care   D3   done 10:02    [YEL]   [ trigger ] [ demo ] [ › ]
Manjunath      abx_course   D1   rings 11:30   [ — ]   [ trigger ] [ demo ] [ › ]
```
Live: SSE refetch on `call_update`/`escalation` + 5s polling fallback. The red flip
must be visible without touching anything (demo moment). Below the table, a
**RECENT CALLS** Panel: last 8 calls as LogLines
(`14:02:11 wound_care D3 Lakshmamma — completed · [YEL]`), so the full call list is
always one glance away without another page.

### 6.4 `/patients/[id]`

Header: name (lg) + `[GRN]` + `caregiver +91… · ward 4 · enrolled 21 Jul` (sm muted).
Panels: **TIMELINE** (LogLines: calls with digit answers inline) · **MEDS** (small
table + AWaRe badges) · **ESCALATIONS** (if any) · actions row:
`[ kannada sheet ] [ fhir json ] [ trigger call ]`.

### 6.5 `/sheet/[id]` — Kannada caregiver print sheet

White background (print), Noto Sans Kannada, black text. Sections: hospital name ·
patient/condition/discharge date · **ಮುಖ್ಯ ಸೂಚನೆಗಳು** (Kannada bullets from the
pre-approved bank — LLM-selected subset when available, else template top-5; a small
`text-disabled` footnote marks `instructions: personalized` vs `standard`) ·
med table (name, ಮೊತ್ತ/dose, days) · follow-up call days (1/3/7/14) · red-flag box:
"ಈ ಲಕ್ಷಣಗಳು ಕಂಡರೆ ತಕ್ಷಣ ಆಸ್ಪತ್ರೆಗೆ ಬನ್ನಿ" + `104 / 108` · footer: consent note +
"triage layer, not an emergency service" (Kannada + English). Browser `@media print`:
hide everything except sheet, A5-ish margins. Sheet content is static per protocol
(lives in the protocol JSON under `"sheet"`) — no LLM.

### 6.6 `/escalations`

Open queue table: time, patient, protocol/day, reasons (danger text), ack state
(`[ ack ]` button → sets acked, shows `acked by nurse01 · 42m` — J2 SLA visible).
Acked section collapsed below. Subscribes to SSE.

### 6.7 `/amr` — stewardship snapshot (J10)

Stats row: `course completion 78%` · `self-medication 12%` · `unreachable 22%` ·
`est. cost ₹96`. Below: three hand-rolled bar rows (div widths, `--color-accent`),
e.g. pill-count buckets `0–3 ▓▓▓▓▓▓░░ 6` · plus one LogLine panel listing recent
AMR-relevant reasons ("8+ pills remain on day 6 of a 5-day course"). No chart library.

### 6.8 `/demo` — Demo Call Console (docs/04 §4)

```
┌─ DEMO CALL — wound_care · patient: Lakshmamma ─────────────┐
│ 14:02:11 ▸ [greet] “Greetings. This is…”            (audio)│
│ 14:02:19 ▸ [confirm_family] “Are you the family?”          │
│ 14:02:25 ◂ pressed 1                                       │
│ 14:02:25 ▸ [q_wound] “How is the wound?”                   │
│                                                            │
│ current question: wound status                             │
│ [ 1 healing ] [ 2 pain/swelling ] [ 3 pus/fever ] ▊        │
└────────────────────────────────────────────────────────────┘
```
Transcript = LogLines (`▸` system plays clip + English gloss, `◂` user input).
Buttons appear **only** as per-question answer choices, enabled only while the server
waits for a digit. Ends with a summary line: `risk: [RED] → escalation created`.
Header carries a permanent `SIMULATED CALL — no real phone involved` marker
(`text-warning xs`).

## 7. Responsive & PWA-lite

- Breakpoint 720px: tables → stacked cards (same data, label above value); topbar nav
  collapses to a single row of text links that wraps; stats grid 2×2.
- `manifest.json` (name, dark bg `#0c0c0e`, 192/512 icons = the two-square logo on
  dark) + theme-color meta. **No service worker** (KISS).
- Fonts via fontsource (offline-safe). Total JS budget: keep it lean — no lib beyond
  the pinned set.
