# 08 — Health Device Integration (Google Fit + Telegram Bot)

## 1. Problem

Post-discharge monitoring is limited to IVR calls (every few days) and
self-reported symptoms via Telegram. Smart health devices collect continuous
vital signs (heart rate, SpO2, sleep, steps) that would dramatically improve
recovery tracking — but device data is siloed in vendor apps.

## 2. Solution: Google Fit as the universal adapter

Almost ALL Android fitness trackers (Mi Band, Amazfit, Noise, boAt, Samsung,
Huawei) sync their data into Google Fit as the central hub. Google Fit exposes
a free REST API with OAuth2 authorization — no SDK needed, no approval process.

**Flow:**
1. Patient opens Telegram bot → /connect_device
2. Bot sends a web link: `PUBLIC_BASE_URL/api/health/fit/authorize?tgid=<telegram_id>`
3. Patient clicks → redirected to Google OAuth consent screen
4. Patient authorizes → Google redirects to `/api/health/fit/callback`
5. Server exchanges code for tokens, stores encrypted refresh token
6. Confirmation page shown → patient returns to Telegram bot
7. Bot (or scheduled job) periodically fetches health data via Google Fit REST API
8. Health data stored in `patient_health_data` table
9. Doctor/nurse views per-patient health trajectory on dashboard

**Fallback for non-Android / non-Google-Fit users:**
- `/upload_report` command: patient sends a PDF/image of health report
- Bot uses Groq vision to extract key metrics
- Stored in `patient_reports` table

## 3. Google Fit API — What data we get

| Metric | Data Type | DataSource ID | What it tells us |
|--------|-----------|---------------|-----------------|
| Heart rate | com.google.heart_rate.bpm | derived:com.google.heart_rate.bpm:merge_max | Resting HR, HR trend |
| Blood oxygen | com.google.oxygen_saturation | raw:com.google.oxygen_saturation:merge_min | SpO2 levels |
| Steps | com.google.step_count.delta | derived:com.google.step_count.delta:merge_daily | Activity level |
| Sleep | com.google.sleep.segment | raw:com.google.sleep.segment:merge_period | Sleep quality |
| Body temp | com.google.body.temperature | derived:com.google.body.temperature:merge_avg | Fever detection |
| Blood pressure | com.google.blood_pressure | derived:com.google.blood_pressure:merge_avg | BP trend |
| Weight | com.google.weight | raw:com.google.weight:merge_avg | Weight changes |

**Scopes needed:**
- `https://www.googleapis.com/auth/fitness.heart_rate.read`
- `https://www.googleapis.com/auth/fitness.oxygen_saturation.read`
- `https://www.googleapis.com/auth/fitness.activity.read`
- `https://www.googleapis.com/auth/fitness.sleep.read`
- `https://www.googleapis.com/auth/fitness.body.read`

## 4. New database tables

```sql
CREATE TABLE patient_health_tokens (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES patients(id),
  hospital_code TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'google_fit',
  access_token TEXT NOT NULL,           -- encrypted at rest (Fernet)
  refresh_token TEXT NOT NULL,          -- encrypted at rest (Fernet)
  token_expiry TEXT NOT NULL,           -- UTC ISO-8601
  scope TEXT,                           -- granted scopes
  connected_at TEXT NOT NULL,
  last_synced_at TEXT,
  UNIQUE(patient_id, provider)
);

CREATE TABLE patient_health_data (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES patients(id),
  hospital_code TEXT NOT NULL,
  metric_type TEXT NOT NULL,            -- 'heart_rate' | 'spo2' | 'steps' | 'sleep' | 'body_temp' | 'blood_pressure' | 'weight'
  value REAL NOT NULL,                  -- numeric value
  unit TEXT NOT NULL,                   -- 'bpm' | '%' | 'count' | 'minutes' | '°C' | 'mmHg' | 'kg'
  recorded_at TEXT NOT NULL,            -- UTC ISO-8601 (when the measurement was taken)
  fetched_at TEXT NOT NULL,             -- UTC ISO-8601 (when we fetched it)
  source TEXT,                          -- device name or 'google_fit'
  UNIQUE(patient_id, metric_type, recorded_at)
);

CREATE TABLE patient_reports (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL REFERENCES patients(id),
  hospital_code TEXT NOT NULL,
  report_type TEXT NOT NULL,            -- 'lab_report' | 'discharge_summary' | 'prescription' | 'other'
  filename TEXT NOT NULL,               -- stored filename
  uploaded_by TEXT NOT NULL,            -- 'patient' | 'nurse' | 'system'
  extracted_data TEXT,                  -- JSON: key-value pairs extracted by LLM
  uploaded_at TEXT NOT NULL
);
```

## 5. New backend modules

```
backend/app/
├── health_fit/
│   ├── __init__.py
│   ├── config.py          # OAuth constants, scope list
│   ├── oauth.py           # OAuth flow: authorize URL, token exchange, refresh
│   ├── client.py          # Google Fit REST API client (fetch metrics)
│   ├── analytics.py       # Trend analysis, anomaly detection, risk enhancement
│   └── models.py          # SQLAlchemy models (patient_health_tokens, patient_health_data)
├── routers/
│   └── health.py          # /api/health/* endpoints
```

## 6. Telegram bot new commands

| Command | What it does |
|---------|-------------|
| `/connect_device` | Sends web link to authorize Google Fit access |
| `/disconnect` | Removes stored tokens |
| `/health` | Shows latest health summary (HR, SpO2, steps, sleep) |
| `/health_trend` | Shows 7-day trend for each metric |
| `/upload_report` | Instructions to send a PDF/image for OCR extraction |

## 7. Dashboard API endpoints

| Method & Path | Auth | What it returns |
|---------------|------|----------------|
| `GET /api/health/fit/authorize?tgid=<id>` | public (starts OAuth) | Redirect to Google |
| `GET /api/health/fit/callback?code=<code>&state=<tgid>` | public (OAuth callback) | Confirmation page |
| `POST /api/health/sync?patient_id=<id>` | staff | Trigger immediate data fetch |
| `GET /api/patients/{id}/health-data` | session | Per-patient health metrics + trends |
| `GET /api/patients/{id}/health-summary` | session | Computed analytics summary |
| `GET /api/health/dashboard` | session | All patients health overview |

## 8. Analytics engine

For each patient, compute:
- **HR trend**: 7-day avg, min, max, resting HR estimate, trend direction
- **SpO2 trend**: 7-day avg, lowest reading, trend direction
- **Activity trend**: daily steps, 7-day avg, trend direction
- **Sleep analysis**: avg duration, consistency, quality estimate
- **Composite health score**: weighted combination of all metrics
- **Risk flags**: sudden SpO2 drop, resting HR spike, zero activity days
- **Recovery trajectory**: comparing current week vs previous week

## 9. Security

- OAuth tokens encrypted with Fernet (derived from SECRET_KEY)
- Tokens only accessible by hospital-scoped queries
- No PII in logs
- Refresh tokens expire → patient must re-authorize (90-day Google default)
- `CALL_ALLOWLIST` principle extended: only enrolled patients can connect devices

## 10. Config additions

```bash
# Google Fit OAuth (optional — unset = health device features disabled)
GOOGLE_FIT_CLIENT_ID=
GOOGLE_FIT_CLIENT_SECRET=
GOOGLE_FIT_REDIRECT_URI=PUBLIC_BASE_URL/api/health/fit/callback
# Encryption key for storing OAuth tokens (auto-generated if not set)
HEALTH_ENCRYPT_KEY=
```
