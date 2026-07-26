"""Telegram bot — polling-based, patient + staff chat with RAG.

Polls getUpdates every 5s. Handles:
- /start — welcome
- /help — commands
- /verify — patient OTP verification (text code, free)
- /staff — staff code verification
- /status — patient info (if verified)
- /meds — medications (if verified)
- /ask <question> — RAG-powered answer
- Free text — context-aware response + symptom detection → escalation
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.db import SessionLocal, now_utc
from app.models import Enrollment, EnrollmentMed, Escalation, Patient
from app.telegram.rag import ask_llm, retrieve
from app.telegram.sessions import (
    Session,
    check_rate_limit,
    format_time,
    generate_otp,
    get_session,
    set_otp,
    verify_otp,
    verify_staff,
)
from app.amr_steward import confirm_meds, report_pill_count
from app.health_fit import PatientHealthData, PatientHealthToken, PatientReport

log = logging.getLogger("telegram.bot")

POLL_TIMEOUT = 5  # long-poll seconds
API = "https://api.telegram.org/bot{token}"

# ── symptom detection keywords ────────────────────────────────────────────────
# Red-flag words in English and Kannada
SYMPTOM_KEYWORDS = {
    "critical": [
        "chest pain", "can't breathe", "cannot breathe", "unconscious", "seizure",
        "heavy bleeding", "severe bleeding", "fainting", "stroke",
        "ಎದೆನೋವು", "ಉಸಿರಾಟ ತೊಂದರೆ", "ಕಿಂಡು ಹೋಗಿದ್ದೇನೆ", "ಸೆಳೆತ",
    ],
    "high": [
        "high fever", "vomiting", "blood", "pus", "wound open", "worsening",
        "dizzy", "dizziness", "breathless", "swelling", "redness",
        "ತೀವ್ರ ಜ್ವರ", "ಬೇಧಿ", "ರಕ್ತ", "ಪುಸ್", "ಗಾಯ ತೆರೆದಿದೆ",
        "ತಲೆಸುತ್ತು", "ಉಸಿರಾಟ ಕಷ್ಟ", "ಊತ",
    ],
    "medium": [
        "pain", "fever", "nausea", "rash", "itching", "not eating",
        "can't sleep", "anxiety", "stomach ache", "headache",
        "ನೋವು", "ಜ್ವರ", "ವಾಂತಿ", "ತುರಿಕೆ", "ತಿನ್ನಲು ಆಗುತ್ತಿಲ್ಲ",
        "ಹೊಟ್ಟೆನೋವು", "ತಲೆನೋವು",
    ],
}

# ── pending OTP state (per user) ──────────────────────────────────────────────
_pending_otp: dict[int, dict] = {}  # {telegram_id: {"phone": ..., "otp": ...}}
_pending_staff: dict[int, bool] = {}
_pending_pill_count: dict[int, bool] = {}  # {telegram_id: True} — awaiting pill count number

# ── alert cooldown (per enrollment_id) — prevent spam ─────────────────────────
_alert_cooldown: dict[str, float] = {}  # {enrollment_id: timestamp of last alert}
ALERT_COOLDOWN_SECONDS = 3600  # 1 hour


def _api(token: str, method: str, **kwargs) -> dict | None:
    url = f"{API.format(token=token)}/{method}"
    try:
        r = httpx.post(url, json=kwargs, timeout=10.0)
        if r.status_code == 200:
            return r.json()
        log.warning("telegram %s %s: %s", method, r.status_code, r.text[:200])
    except Exception as e:
        log.warning("telegram %s failed: %s", method, e)
    return None


def _send(token: str, chat_id: int, text: str) -> None:
    _api(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)


def _lookup_patient(phone: str) -> dict | None:
    """Look up patient by caregiver phone number."""
    s = SessionLocal()
    try:
        p = s.query(Patient).filter(Patient.caregiver_phone == phone).first()
        if not p:
            return None
        en = s.query(Enrollment).filter(Enrollment.patient_id == p.id).first()
        meds = []
        if en:
            for m in s.query(EnrollmentMed).filter(EnrollmentMed.enrollment_id == en.id).all():
                meds.append(f"{m.med_name} ({m.med_type}, {m.doses_per_day}x/day, {m.course_days or '?'} days)")

        return {
            "name": p.name,
            "age": p.age,
            "sex": p.sex,
            "phone": p.caregiver_phone,
            "condition": en.condition_label if en else "unknown",
            "protocol": en.protocol_id if en else "unknown",
            "discharge_date": en.discharge_date if en else "unknown",
            "ward": en.ward if en else "unknown",
            "meds": ", ".join(meds) if meds else "none prescribed",
        }
    except Exception as e:
        log.warning("patient lookup failed: %s", e)
        return None
    finally:
        s.close()


def _detect_language(text: str) -> str:
    """Simple Kannada detection — check for Kannada Unicode range."""
    kannada_chars = sum(1 for c in text if "\u0C80" <= c <= "\u0CFF")
    return "kn" if kannada_chars > 0 else "en"


def _detect_symptoms(text: str) -> str | None:
    """Detect symptom severity from text. Returns 'critical'/'high'/'medium'/None."""
    t = text.lower()
    for level in ("critical", "high", "medium"):
        for kw in SYMPTOM_KEYWORDS[level]:
            if kw.lower() in t:
                return level
    return None


def _create_alert(patient_data: dict, message: str, severity: str) -> str | None:
    """Create an escalation from a Telegram symptom report. Returns escalation_id.
    Dedup: skips if an open escalation already exists for this enrollment,
    or if the last alert was within ALERT_COOLDOWN_SECONDS."""
    s = SessionLocal()
    try:
        en = s.query(Enrollment).filter(
            Enrollment.patient_id == s.query(Patient).filter(
                Patient.caregiver_phone == patient_data["phone"]
            ).first().id
        ).first() if patient_data else None

        if not en:
            return None

        # dedup: check for existing open escalation
        existing = s.query(Escalation).filter(
            Escalation.enrollment_id == en.id,
            Escalation.status == "open",
        ).first()
        if existing:
            log.info("skipping alert — open escalation %s exists for enrollment %s", existing.id[:8], en.id[:8])
            return None

        # cooldown: skip if last alert was less than1 hour ago
        now = time.time()
        last_alert = _alert_cooldown.get(en.id, 0)
        if now - last_alert < ALERT_COOLDOWN_SECONDS:
            log.info("skipping alert — cooldown active for enrollment %s", en.id[:8])
            return None

        esc = Escalation(
            id=uuid.uuid4().hex,
            hospital_code=settings.HOSPITAL_CODE,
            enrollment_id=en.id,
            call_id=None,
            level="red" if severity in ("critical", "high") else "yellow",
            reasons=json.dumps([f"Telegram symptom report ({severity}): {message[:100]}"]),
            status="open",
            created_at=now_utc(),
        )
        s.add(esc)
        s.commit()
        _alert_cooldown[en.id] = now

        # publish SSE event so dashboard updates live
        try:
            from app.events import publish
            publish("escalation", esc.id)
        except Exception:
            pass

        return esc.id
    except Exception as e:
        log.warning("failed to create alert: %s", e)
        s.rollback()
        return None
    finally:
        s.close()


def _send_alert_to_group(patient_data: dict, message: str, severity: str, esc_id: str) -> None:
    """Send symptom alert to Telegram group."""
    chat_id = settings.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(severity, "⚪")
    token = settings.TELEGRAM_BOT_TOKEN

    text = (
        f"{icon} PATIENT ALERT — {settings.HOSPITAL_NAME}\n"
        f"Patient: {patient_data['name']} (enrollment {esc_id[:8]})\n"
        f"Severity: {severity.upper()}\n"
        f"Message: \"{message[:200]}\"\n"
        f"Condition: {patient_data['condition']}\n"
        f"Protocol: {patient_data['protocol']}\n"
        f"Dashboard: {settings.PUBLIC_BASE_URL}/escalations\n"
        f"Call patient: tel:{patient_data['phone']}"
    )
    _api(token, "sendMessage", chat_id=int(chat_id), text=text, disable_web_page_preview=True)


# ── message handler ───────────────────────────────────────────────────────────

def _handle_message(token: str, msg: dict[str, Any]) -> None:
    chat_id = msg["chat"]["id"]
    telegram_id = msg["from"]["id"]
    text = msg.get("text", "").strip()[:1000]  # limit input length
    first_name = msg["from"].get("first_name", "")

    if not text:
        return

    session = get_session(telegram_id)

    # rate limit
    allowed, retry = check_rate_limit(session)
    if not allowed:
        _send(token, chat_id, f"⏱ Rate limit hit. Try again in {format_time(retry)}.")
        return

    # ── pill count number input (stateful) ────────────────────────────────────
    if telegram_id in _pending_pill_count:
        del _pending_pill_count[telegram_id]
        if not session.verified or not session.phone:
            _send(token, chat_id, "Session expired. Send /verify to link your phone.")
            return
        # parse number
        try:
            count = int(text.strip())
            if count < 0:
                raise ValueError
        except ValueError:
            _send(token, chat_id, "Please enter a valid number (e.g., 12)")
            return
        msg = report_pill_count(session.phone, count)
        _send(token, chat_id, msg)
        return

    # ── OTP verification flow (stateful) ──────────────────────────────────────
    if telegram_id in _pending_otp:
        pending = _pending_otp[telegram_id]
        # only compare as OTP if we're actually awaiting an OTP (not awaiting phone)
        if pending.get("state") == "awaiting_otp" and pending.get("otp"):
            if text == pending["otp"]:
                session.phone = pending["phone"]
                session.verified = True
                session.verified_at = time.time()
                del _pending_otp[telegram_id]
                _send(token, chat_id, (
                    f"✓ Verified! Welcome, {first_name}.\n\n"
                    "You can now ask about your medications and recovery.\n"
                    "Try: /meds, /status, or just type a question."
                ))
                return
            else:
                # allow retry up to 3 times
                pending["attempts"] = pending.get("attempts", 0) + 1
                if pending["attempts"] >= 3:
                    del _pending_otp[telegram_id]
                    _send(token, chat_id, "✗ Too many failed attempts. Send /verify to try again.")
                else:
                    _send(token, chat_id, f"✗ Wrong code. Try again ({3 - pending['attempts']} attempts left).")
                return
        # if awaiting_phone, fall through to phone handler below

    # ── staff code flow ───────────────────────────────────────────────────────
    if telegram_id in _pending_staff:
        if verify_staff(session, text):
            del _pending_staff[telegram_id]
            _send(token, chat_id, (
                "✓ Staff access granted.\n\n"
                "Ask me about protocols, AMR guidelines, dosing, or clinical workflows.\n"
                "Try: /ask wound care protocol, /ask AMR guidelines"
            ))
        else:
            del _pending_staff[telegram_id]
            _send(token, chat_id, "✗ Invalid staff code.")
        return

    # ── commands ──────────────────────────────────────────────────────────────
    if text.startswith("/"):
        cmd = text.split()[0].lower()

        if cmd == "/start":
            _send(token, chat_id, (
                f"Welcome to Aarogya Bandhu, {first_name}! 🏥\n\n"
                "I help patients with recovery and staff with protocols.\n\n"
                "Patient? Send /verify to link your phone number.\n"
                "Staff? Send /staff to enter access code.\n\n"
                "Commands:\n"
                "/verify — verify as patient (code sent here)\n"
                "/staff — verify as staff (access code)\n"
                "/meds — see your medications\n"
                "/status — see your patient info\n"
                "/confirm — confirm you took your meds\n"
                "/pills — report remaining pill count\n"
                "/ask <question> — ask about health or protocols\n"
                "/help — show this message\n\n"
                "Feeling unwell? Just tell me — I'll alert the hospital team."
            ))
            return

        if cmd == "/help":
            _send(token, chat_id, (
                "Commands:\n"
                "/verify — verify your phone number (code sent here)\n"
                "/staff — enter staff access code\n"
                "/meds — see your medications\n"
                "/status — see your patient info\n"
                "/confirm — confirm you took your meds today\n"
                "/pills — report remaining pill count\n"
                "/connect_device — link your smart watch/fitness band\n"
                "/health — see your health data from connected device\n"
                "/disconnect — unlink your health device\n"
                "/ask <question> — ask about health, medications, or protocols\n\n"
                "Or just type a question in Kannada or English!\n\n"
                "If you feel unwell, just tell me — I'll alert the hospital team."
            ))
            return

        if cmd == "/verify":
            _send(token, chat_id, (
                "📱 Please enter your phone number in E.164 format:\n"
                "Example: +919353808767"
            ))
            # set state to expect phone number
            _pending_otp[telegram_id] = {"state": "awaiting_phone", "otp": None}
            return

        if cmd == "/staff":
            _send(token, chat_id, "🔑 Enter staff access code:")
            _pending_staff[telegram_id] = True
            return

        if cmd == "/meds":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            patient = _lookup_patient(session.phone)
            if not patient:
                _send(token, chat_id, "No patient found for your phone number.")
                return
            _send(token, chat_id, (
                f"📋 Medications for {patient['name']}:\n\n"
                f"{patient['meds']}\n\n"
                f"Condition: {patient['condition']}\n"
                f"Protocol: {patient['protocol']}\n"
                f"Discharge: {patient['discharge_date']}"
            ))
            return

        if cmd == "/confirm":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            msg = confirm_meds(session.phone)
            _send(token, chat_id, msg)
            return

        if cmd == "/pills":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            _pending_pill_count[telegram_id] = True
            _send(token, chat_id, "🔢 ಉಳಿದ ಮಾತ್ರೆಗಳ ಸಂಖ್ಯೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ (ಉದಾ: 12)")
            return

        if cmd == "/status":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            patient = _lookup_patient(session.phone)
            if not patient:
                _send(token, chat_id, "No patient found for your phone number.")
                return
            _send(token, chat_id, (
                f"📊 Patient Info:\n\n"
                f"Name: {patient['name']}\n"
                f"Age: {patient['age'] or '—'} · Sex: {patient['sex'] or '—'}\n"
                f"Condition: {patient['condition']}\n"
                f"Protocol: {patient['protocol']}\n"
                f"Ward: {patient['ward']}\n"
                f"Discharge: {patient['discharge_date']}"
            ))
            return

        if cmd == "/ask":
            query = text[len("/ask"):].strip()
            if not query:
                _send(token, chat_id, "Usage: /ask <your question>")
                return
            # fall through to RAG below
            text = query

        if cmd == "/connect_device":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            patient = _lookup_patient(session.phone)
            if not patient:
                _send(token, chat_id, "No patient found for your phone number.")
                return
            # Check if already connected
            s = SessionLocal()
            try:
                p = s.query(Patient).filter(Patient.caregiver_phone == session.phone).first()
                if p:
                    existing = s.query(PatientHealthToken).filter(
                        PatientHealthToken.patient_id == p.id
                    ).first()
                    if existing:
                        _send(token, chat_id, (
                            "✓ Your device is already connected!\n\n"
                            "Last synced: " + (existing.last_synced_at or "never") + "\n\n"
                            "Send /health to see your data.\n"
                            "Send /disconnect to unlink."
                        ))
                        return
            finally:
                s.close()
            # Check if Google Fit is configured
            if not settings.GOOGLE_FIT_CLIENT_ID:
                _send(token, chat_id, (
                    "📱 Health device integration is not yet configured.\n"
                    "The hospital admin needs to set up Google Fit OAuth.\n\n"
                    "For now, you can still use /meds, /confirm, /pills, and /ask."
                ))
                return
            # Send OAuth link
            from app.routers.health import get_pending_connection
            base = settings.PUBLIC_BASE_URL or "http://localhost:8000"
            oauth_url = f"{base}/api/health/fit/authorize?tgid={telegram_id}"
            _send(token, chat_id, (
                "📱 Connect Your Health Device\n\n"
                "Link your smart watch or fitness band (Mi Band, Amazfit, "
                "Samsung, Noise, etc.) to share health data with your care team.\n\n"
                "Supported devices: Any device that syncs with Google Fit "
                "(most Android fitness trackers do).\n\n"
                "1. Tap the link below to authorize\n"
                "2. Sign in with your Google account\n"
                "3. Allow access to health data\n"
                "4. Come back here and send /connect_device again\n\n"
                f"🔗 Authorize: {oauth_url}\n\n"
                "Your data is encrypted and only visible to your care team."
            ))
            return

        if cmd == "/health":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            # First check if there's a pending OAuth connection to link
            from app.routers.health import get_pending_connection
            pending = get_pending_connection(telegram_id)
            if pending:
                # Link the connection to the patient
                s = SessionLocal()
                try:
                    p = s.query(Patient).filter(Patient.caregiver_phone == session.phone).first()
                    if p:
                        # Store the tokens
                        token_row = PatientHealthToken(
                            id=uuid.uuid4().hex,
                            patient_id=p.id,
                            hospital_code=settings.HOSPITAL_CODE,
                            provider="google_fit",
                            access_token=pending["access_token"],
                            refresh_token=pending["refresh_token"],
                            token_expiry=pending["expiry"],
                            scope=pending["scope"],
                            connected_at=pending["connected_at"],
                        )
                        s.add(token_row)
                        s.commit()
                        _send(token, chat_id, (
                            "✓ Device linked successfully!\n\n"
                            "Fetching your health data now..."
                        ))
                        # Now fetch initial data
                        _fetch_and_send_health(token, chat_id, session.phone, telegram_id)
                        return
                    else:
                        _send(token, chat_id, "No patient found for your phone number.")
                        return
                finally:
                    s.close()

            # No pending connection — show existing health data
            _fetch_and_send_health(token, chat_id, session.phone, telegram_id)
            return

        if cmd == "/disconnect":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify first. Send /verify to link your phone.")
                return
            s = SessionLocal()
            try:
                p = s.query(Patient).filter(Patient.caregiver_phone == session.phone).first()
                if p:
                    deleted = s.query(PatientHealthToken).filter(
                        PatientHealthToken.patient_id == p.id
                    ).delete()
                    s.commit()
                    if deleted:
                        _send(token, chat_id, "✓ Health device disconnected. Your data has been removed.")
                    else:
                        _send(token, chat_id, "No device was connected.")
                else:
                    _send(token, chat_id, "No patient found for your phone number.")
            finally:
                s.close()
            return
        else:
            _send(token, chat_id, f"Unknown command: {cmd}. Send /help for available commands.")
            return

    # ── handle phone number input (from /verify flow) ─────────────────────────
    if telegram_id in _pending_otp and _pending_otp[telegram_id].get("state") == "awaiting_phone":
        phone = text.strip()
        if not phone.startswith("+") or len(phone) < 10:
            _send(token, chat_id, "Invalid phone format. Please use E.164: +91XXXXXXXXXX")
            return
        # check if patient exists
        patient = _lookup_patient(phone)
        if not patient:
            _send(token, chat_id, (
                "✗ No patient found with this phone number.\n"
                "Please check and try again, or contact the hospital."
            ))
            del _pending_otp[telegram_id]
            return
        # generate OTP and call
        otp = generate_otp()
        _pending_otp[telegram_id] = {"phone": phone, "otp": otp, "attempts": 0, "state": "awaiting_otp"}
        # call via Twilio
        _call_otp(token, chat_id, phone, otp)
        return

    # ── RAG: free text or /ask ────────────────────────────────────────────────
    patient_ctx = None
    if session.verified and session.phone:
        patient_ctx = _lookup_patient(session.phone)

    # symptom detection for verified patients
    if patient_ctx:
        severity = _detect_symptoms(text)
        if severity:
            esc_id = _create_alert(patient_ctx, text, severity)
            if esc_id:
                _send_alert_to_group(patient_ctx, text, severity, esc_id)
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(severity, "⚪")
                _send(token, chat_id, (
                    f"{icon} Your symptom has been reported to the hospital team.\n"
                    f"Severity: {severity.upper()}\n"
                    f"Reference: #{esc_id[:8]}\n\n"
                    f"If this is an emergency, call 104 or 108 immediately.\n"
                    f"A nurse will follow up with you shortly."
                ))
                # still send RAG response
                context = retrieve(text, patient_ctx)
                rag_response = ask_llm(text, context, patient_ctx, is_staff=session.staff)
                _send(token, chat_id, rag_response)
                return

    context = retrieve(text, patient_ctx)
    response = ask_llm(text, context, patient_ctx, is_staff=session.staff)
    _send(token, chat_id, response)


def _call_otp(token: str, chat_id: int, phone: str, otp: str) -> None:
    """Send OTP as text in Telegram (free, no Twilio cost)."""
    _send(token, chat_id, (
        f"🔑 Your verification code: {otp}\n\n"
        f"Enter this code to verify your phone number."
    ))


def _fetch_and_send_health(token: str, chat_id: int, phone: str, telegram_id: int) -> None:
    """Fetch health data from Google Fit and send summary to patient."""
    from datetime import datetime, timedelta, timezone
    from app.health_fit.client import fetch_all_metrics
    from app.health_fit.analytics import compute_health_summary
    from app.routers.health import _decrypt, _get_fernet

    s = SessionLocal()
    try:
        p = s.query(Patient).filter(Patient.caregiver_phone == phone).first()
        if not p:
            _send(token, chat_id, "No patient found.")
            return

        token_row = s.query(PatientHealthToken).filter(
            PatientHealthToken.patient_id == p.id
        ).first()
        if not token_row:
            _send(token, chat_id, (
                "No device connected. Send /connect_device to link your smart watch."
            ))
            return

        # Decrypt access token
        access_token = _decrypt(token_row.access_token)

        # Check if token needs refresh
        if datetime.fromisoformat(token_row.token_expiry) < datetime.now(timezone.utc):
            from app.health_fit.oauth import refresh_access_token
            refreshed = refresh_access_token(token_row.refresh_token, _get_fernet())
            if not refreshed:
                _send(token, chat_id, (
                    "⚠️ Your device connection expired.\n"
                    "Send /connect_device to re-authorize."
                ))
                return
            access_token = refreshed["access_token"]
            token_row.access_token = _encrypt(access_token)
            token_row.token_expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=refreshed.get("expires_in", 3600))
            ).isoformat()
            s.commit()

        # Fetch last 7 days
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        metrics = fetch_all_metrics(access_token, start.isoformat(), end.isoformat())

        # Store fetched data
        stored_count = 0
        for metric_type, points in metrics.items():
            for point in points:
                try:
                    row = PatientHealthData(
                        id=uuid.uuid4().hex,
                        patient_id=p.id,
                        hospital_code=settings.HOSPITAL_CODE,
                        metric_type=metric_type,
                        value=point["value"],
                        unit={"heart_rate": "bpm", "spo2": "%", "steps": "count",
                              "sleep": "minutes", "body_temp": "°C"}.get(metric_type, ""),
                        recorded_at=point["recorded_at"],
                        source=point.get("source", "google_fit"),
                    )
                    s.add(row)
                    stored_count += 1
                except Exception:
                    pass
        s.commit()

        # Update last_synced_at
        token_row.last_synced_at = now_utc()
        s.commit()

        # Compute summary
        all_rows = s.query(PatientHealthData).filter(
            PatientHealthData.patient_id == p.id,
        ).order_by(PatientHealthData.recorded_at.desc()).limit(200).all()

        row_dicts = [
            {"metric_type": r.metric_type, "value": r.value, "recorded_at": r.recorded_at}
            for r in all_rows
        ]
        summary = compute_health_summary(row_dicts)

        # Format message
        lines = [f"📊 Health Summary for {p.name}\n"]

        if "heart_rate" in summary:
            hr = summary["heart_rate"]
            lines.append(f"💓 Heart Rate: {hr.get('latest', '—')} bpm (avg {hr.get('avg_7d', '—')})")
            if hr.get("flags"):
                lines.append(f"   ⚠️ {', '.join(hr['flags'])}")

        if "spo2" in summary:
            sp = summary["spo2"]
            lines.append(f"🫁 SpO2: {sp.get('latest', '—')}% (avg {sp.get('avg_7d', '—')}%)")
            if sp.get("flags"):
                lines.append(f"   ⚠️ {', '.join(sp['flags'])}")

        if "steps" in summary:
            st = summary["steps"]
            lines.append(f"🚶 Steps today: {int(st.get('today', 0))} (avg {int(st.get('avg_7d', 0))})")
            if st.get("flags"):
                lines.append(f"   ⚠️ {', '.join(st['flags'])}")

        if "sleep" in summary:
            sl = summary["sleep"]
            lines.append(f"😴 Sleep: {sl.get('latest_hours', '—')}h (avg {sl.get('avg_hours', '—')}h)")
            if sl.get("flags"):
                lines.append(f"   ⚠️ {', '.join(sl['flags'])}")

        if "body_temp" in summary:
            bt = summary["body_temp"]
            lines.append(f"🌡️ Temperature: {bt.get('latest', '—')}°C")
            if bt.get("flags"):
                lines.append(f"   ⚠️ {', '.join(bt['flags'])}")

        score = summary.get("health_score", 0)
        lines.append(f"\n🏥 Health Score: {score}/100")
        if summary.get("overall_flags"):
            lines.append(f"⚠️ Flags: {', '.join(summary['overall_flags'])}")

        lines.append(f"\n📡 {stored_count} data points synced")
        lines.append("Data is shared with your care team for better recovery monitoring.")

        _send(token, chat_id, "\n".join(lines))

    except Exception as e:
        log.warning("health fetch failed: %s", e)
        _send(token, chat_id, "⚠️ Failed to fetch health data. Please try again later.")
    finally:
        s.close()


# ── polling loop ──────────────────────────────────────────────────────────────

async def poll_loop() -> None:
    """Background task: long-polls Telegram getUpdates."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        log.info("TELEGRAM_BOT_TOKEN not set — polling disabled")
        return

    log.info("telegram polling started")
    offset = 0

    async with httpx.AsyncClient() as client:
        while True:
            try:
                url = f"{API.format(token=token)}/getUpdates"
                r = await client.get(
                    url,
                    params={"offset": offset, "timeout": POLL_TIMEOUT},
                    timeout=POLL_TIMEOUT + 5,
                )
                if r.status_code == 200:
                    data = r.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message")
                        if msg:
                            # run sync handler in thread to avoid blocking
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, _handle_message, token, msg)
                else:
                    log.warning("getUpdates %s: %s", r.status_code, r.text[:100])
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                log.info("telegram polling stopped")
                break
            except Exception as e:
                log.warning("poll error: %s", e)
                await asyncio.sleep(5)
