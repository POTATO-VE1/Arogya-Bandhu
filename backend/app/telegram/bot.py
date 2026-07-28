"""Telegram bot — polling-based, patient + staff chat with RAG.

Polls getUpdates every 5s. Features:
- Multi-lingual (Kannada & English) response routing
- Persistent authentication (auto-load latest patient record on restart)
- Multi-patient resolution for shared family phone numbers
- Structured diet collection & Google Fit integration
- Automatic SOS & symptom escalation to live Doctor Dashboard via SSE

T12 follow-ups in this file:
- Wrapped the whole handler in a try/except so unhandled errors
  log + send a generic "something went wrong" rather than silently
  swallowing messages.
- Normalize phone numbers consistently (12-digit with leading country
  code → strip the leading 0/91 and add +91; bare 10-digit → +91).
- Bounded the in-memory `_pending_family_selection` dict to avoid leaks.
- `/reset` now uses `reset_session()` from sessions.py so attempts,
  language, staff, admin flags all clear.
- Auth-failure attempts persisted to DB so a `/reset` actually
  resets them (previously the in-memory `attempts` would survive
  a reset because it wasn't on the Session dataclass).
- Active chat path now `save_session(session)` so a server restart
  preserves the last user message in DB.

Note: `graph.py` + `nodes.py` + `state.py` are a parallel
LangGraph implementation that's not wired into this flow. They
remain importable (so `from app.telegram.bot import poll_loop`
keeps working) but are unused in production.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.config import settings
from app.db import SessionLocal, now_utc
from app.models import Enrollment, Escalation, FollowupCall, Patient, TelegramSession
from app.telegram.admin_bot import (
    handle_admin_message,
    is_verified_admin,
    request_admin_verify,
    handle_contact_share,
)
from app.telegram.rag import ask_llm, retrieve
from app.telegram.sessions import (
    MAX_AUTH_ATTEMPTS,
    Session,
    check_rate_limit,
    format_time,
    get_patient_report_by_id,
    get_patient_reports,
    get_session,
    lookup_patients_by_phone,
    reset_session,
    save_session,
    verify_staff,
)

log = logging.getLogger("telegram.bot")

POLL_TIMEOUT = 5
API = "https://api.telegram.org/bot{token}"

# ── symptom detection keywords ────────────────────────────────────────────────
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

MAX_FAMILY_SELECTION = 1000  # bound the in-memory pending selection
_pending_family_selection: dict[int, list[dict]] = {}
_pending_staff: dict[int, bool] = {}
_alert_cooldown: dict[str, float] = {}
ALERT_COOLDOWN_SECONDS = 3600


def _evict_family_selection_if_full() -> None:
    if len(_pending_family_selection) > MAX_FAMILY_SELECTION:
        for k in list(_pending_family_selection.keys())[:100]:
            _pending_family_selection.pop(k, None)


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
    _api(token, "sendMessage", chat_id=chat_id, text=text,
         disable_web_page_preview=True)


def _detect_language(text: str) -> str:
    """'kn' if any Kannada Unicode char, else 'en'."""
    kannada_chars = sum(1 for c in text if "\u0C80" <= c <= "\u0CFF")
    return "kn" if kannada_chars > 0 else "en"


def _detect_sos(text: str) -> bool:
    t = text.lower()
    sos_en = ["help", "sos", "emergency", "need help", "not ok", "getting worse",
              "cant breathe", "urgent", "danger", "dying", "very sick", "call ambulance"]
    sos_kn = ["ಸಹಾಯ", "ಅಪಾಯ", "ತುರ್ತು", "ಸಹಾಯ ಬೇಕು", "ಕೆಟ್ಟಾಗಿದೆ",
              "ಉಸಿರಾಟ ಆಗುತ್ತಿಲ್ಲ", "ಆಸ್ಪತ್ರೆಗೆ ಕರೆಯಿರಿ"]
    return any(w in t for w in sos_en + sos_kn)


def _detect_symptoms(text: str) -> str | None:
    t = text.lower()
    for level in ("critical", "high", "medium"):
        for kw in SYMPTOM_KEYWORDS[level]:
            if kw.lower() in t:
                return level
    return None


def _normalise_phone(raw: str) -> str | None:
    """Phone normalisation: accept '+CC...', 'CC...', '0CC...', or bare
    10-digit. Always returns E.164 with a leading '+' or None if the input
    doesn't look like a phone number at all."""
    if not raw:
        return None
    s = re.sub(r"[\s\-\(\)]", "", raw)
    m = re.match(r"^\+?(\d{10,15})$", s)
    if not m:
        return None
    digits = m.group(1)
    # Strip leading 0 (trunk prefix) or country code 91 if it looks like an
    # Indian mobile without a leading +.
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10:
        return None
    return "+91" + digits


def _create_alert(patient_data: dict, message: str, severity: str) -> str | None:
    """Create an escalation and publish SSE event to Doctor Dashboard."""
    s = SessionLocal()
    try:
        p_id = patient_data.get("patient_id")
        en = s.query(Enrollment).filter(Enrollment.patient_id == p_id).first() if p_id else None
        if not en:
            return None

        existing = s.query(Escalation).filter(
            Escalation.enrollment_id == en.id,
            Escalation.status == "open",
        ).first()
        if existing:
            return existing.id

        now = time.time()
        last_alert = _alert_cooldown.get(en.id, 0)
        if now - last_alert < ALERT_COOLDOWN_SECONDS:
            return None

        level = "red" if severity in ("critical", "high") else "yellow"
        esc = Escalation(
            id=uuid.uuid4().hex,
            hospital_code=settings.HOSPITAL_CODE,
            enrollment_id=en.id,
            call_id=None,
            level=level,
            reasons=json.dumps([f"Telegram symptom report ({severity}): {message[:100]}"]),
            status="open",
            created_at=now_utc(),
        )
        s.add(esc)
        s.commit()
        _alert_cooldown[en.id] = now

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
    """Send alert to Telegram hospital group if configured.

    Security: the phone is MASKED before going out. The unmasked number
    only ever lives in the DB behind session auth — never in a Telegram
    group, where screenshots and forwards are uncontrolled.
    """
    chat_id = settings.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    icon = {"critical": "[CRIT]", "high": "[HIGH]", "medium": "[MED]"}.get(severity, "[INFO]")
    raw_phone = patient_data.get("phone", "")
    masked = (f"{raw_phone[:6]}•••••{raw_phone[-3:]}"
              if len(raw_phone) > 6 else "••••")
    text = (
        f"{icon} PATIENT ALERT — {settings.HOSPITAL_NAME}\n"
        f"Patient: {patient_data['name']} (enrollment {esc_id[:8]})\n"
        f"Severity: {severity.upper()}\n"
        f"Message: \"{message[:200]}\"\n"
        f"Condition: {patient_data['condition']}\n"
        f"Dashboard: {settings.PUBLIC_BASE_URL}/escalations\n"
        f"Caregiver: {masked}"
    )
    _api(settings.TELEGRAM_BOT_TOKEN, "sendMessage",
         chat_id=int(chat_id), text=text, disable_web_page_preview=True)


def _send_alert_to_doctors(patient_data: dict, message: str, severity: str, esc_id: str) -> None:
    """Send a direct Telegram DM to every doctor in the hospital who has
    linked their Telegram account. Doctors can opt-in by messaging the
    bot and using /link <username> after admin creates their account.
    """
    from app.models import User
    s = SessionLocal()
    try:
        doctors = s.query(User).filter(
            User.hospital_code == settings.HOSPITAL_CODE,
            User.role == "doctor",
            User.telegram_id.isnot(None),
        ).all()
        if not doctors:
            return
        icon = {"critical": "[CRIT]", "high": "[HIGH]", "medium": "[MED]"}.get(severity, "[INFO]")
        text = (
            f"{icon} Direct alert from your patient\n\n"
            f"Patient: {patient_data['name']}\n"
            f"Condition: {patient_data['condition']}\n"
            f"Severity: {severity.upper()}\n"
            f"Message: \"{message[:200]}\"\n\n"
            f"Open the doctor's dashboard: {settings.PUBLIC_BASE_URL}/escalations\n"
            f"Escalation ID: {esc_id[:8]}\n\n"
            f"Reply via bot: /reply {esc_id[:8]} <your note>"
        )
        for d in doctors:
            try:
                _api(settings.TELEGRAM_BOT_TOKEN, "sendMessage",
                     chat_id=int(d.telegram_id), text=text,
                     disable_web_page_preview=True)
            except Exception as e:
                log.warning("doctor DM failed for %s: %s", d.username, e)
    finally:
        s.close()


def notify_patient_escalation_resolved(escalation_id: str, note: str) -> bool:
    """Called by the escalation-resolve API path: DM the patient that
    their escalation has been resolved and include the doctor's note.
    Returns True if the message was sent.
    """
    from app.models import Escalation, Enrollment, TelegramSession
    s = SessionLocal()
    try:
        x = s.query(Escalation).filter(Escalation.id == escalation_id).first()
        if not x:
            return False
        en = s.query(Enrollment).filter(Enrollment.id == x.enrollment_id).first()
        if not en:
            return False
        ts = s.query(TelegramSession).filter(
            TelegramSession.patient_id == en.patient_id,
            TelegramSession.is_verified == 1,
        ).first()
        if not ts or not ts.telegram_id:
            return False
        lang = ts.preferred_lang or "en"
        if lang == "kn":
            text = (
                "[OK] ನಿಮ್ಮ ತುರ್ತು ಎಚ್ಚರಿಕೆಯನ್ನು ವೈದ್ಯರು ಪರಿಶೀಲಿಸಿದ್ದಾರೆ.\n\n"
                f"[N] ವೈದ್ಯರ ಸಲಹೆ: {note[:500]}\n\n"
                "ಏನಾದರೂ ಹೊಸ ತೊಂದರೆ ಇದ್ದರೆ ತಿಳಿಸಿ. ತುರ್ತು ಸಂದರ್ಭದಲ್ಲಿ 104/108 ಗೆ ಕರೆ ಮಾಡಿ."
            )
        else:
            text = (
                "[OK] Your alert has been reviewed by a doctor.\n\n"
                f"[N] Doctor's note: {note[:500]}\n\n"
                "Let me know if anything new comes up. For emergencies call 104/108."
            )
        _api(settings.TELEGRAM_BOT_TOKEN, "sendMessage",
             chat_id=int(ts.telegram_id), text=text,
             disable_web_page_preview=True)
        return True
    except Exception as e:
        log.warning("notify_patient_escalation_resolved failed for %s: %s", escalation_id, e)
        return False
    finally:
        s.close()


# ── main message handler ──────────────────────────────────────────────────────

def _handle_message(token: str, msg: dict[str, Any]) -> None:
    chat_id = msg["chat"]["id"]
    telegram_id = msg["from"]["id"]
    raw_text = msg.get("text", "").strip()
    first_name = msg["from"].get("first_name", "")

    # Contact share (Telegram sends contact as a regular message with
    # msg["contact"] = { phone_number, first_name, ... }).
    contact = msg.get("contact")
    if contact:
        phone = contact.get("phone_number", "")
        if handle_contact_share(telegram_id, phone):
            _send(token, chat_id, "[OK] Admin verified! You can now use admin commands:\n/create, /list, /delete, /adminhelp")
        else:
            _send(token, chat_id, "[X] Phone number doesn't match admin access.")
        return

    if not raw_text:
        return
    text = raw_text[:500]

    session = get_session(telegram_id)
    detected = _detect_language(text)
    # Bilingual stickiness: the persisted preferred_lang wins unless the
    # patient has written in a different language 3 times in a row. This
    # way an English hiccup doesn't yank a Kannada-speaking patient into
    # English mid-conversation. Use /lang en or /lang kn for an explicit
    # switch.
    if not session.preferred_lang:
        session.preferred_lang = detected
        session.lang_streak = 0
    elif detected and detected != session.preferred_lang:
        session.lang_streak = (getattr(session, "lang_streak", 0) or 0) + 1
        if session.lang_streak >= 3:
            session.preferred_lang = detected
            session.lang_streak = 0
    else:
        session.lang_streak = 0
    lang = session.preferred_lang or detected

    # Rate limit (in-memory window)
    allowed, retry = check_rate_limit(session)
    if not allowed:
        _send(token, chat_id, f"[!] Rate limit hit. Try again in {format_time(retry)}.")
        return

    # ── /admin command ────────────────────────────────────────────────────
    if text.lower().strip() == "/admin":
        if is_verified_admin(telegram_id):
            _send(token, chat_id, "[OK] You are already verified as admin.\n/create, /list, /delete, /adminhelp")
        else:
            request_admin_verify(telegram_id)
            _send(token, chat_id, (
                "[LOCK] Admin Verification Required\n\n"
                "Please share your contact (phone number) to verify admin access.\n"
                "Tap the button below or send /cancel to abort."
            ))
        return

    # ── Admin bot (verified only) ──────────────────────────────────────────
    if is_verified_admin(telegram_id):
        try:
            if handle_admin_message(token, telegram_id, chat_id, text):
                return
        except Exception as e:
            log.exception("admin bot handler failed: %s", e)
            _send(token, chat_id, f"[X] Admin command failed: {e}")
            return

    # ── Staff access code flow ───────────────────────────────────────────
    if telegram_id in _pending_staff:
        if not settings.TELEGRAM_STAFF_CODE:
            _send(token, chat_id, "[X] Staff access not configured on this deployment. Ask an admin to set TELEGRAM_STAFF_CODE.")
        elif verify_staff(session, text):
            del _pending_staff[telegram_id]
            _send(token, chat_id, "[OK] Staff access granted. Ask me about protocols or clinical guidelines.")
        else:
            del _pending_staff[telegram_id]
            _send(token, chat_id, "[X] Invalid staff code.")
        return

    # ── Commands ──────────────────────────────────────────────────────────
    if text.startswith("/"):
        cmd = text.split()[0].lower()

        if cmd == "/start":
            if session.verified and session.patient_id:
                patient = get_patient_report_by_id(session.patient_id)
                if patient:
                    if lang == "kn":
                        _send(token, chat_id, (
                            f"ನಮಸ್ಕಾರ {patient['name']}!\n\n"
                            f"ನಿಮ್ಮ ಚೇತರಿಕೆಯ ಮಾಹಿತಿ:\n"
                            f"[+] ಸ್ಥಿತಿ: {patient['condition']}\n"
                            f"[R] ಔಷಧಿಗಳು: {patient['meds']}\n\n"
                            "ನಿಮ್ಮ ಆರೋಗ್ಯ, ಆಹಾರ ಅಥವಾ ಔಷಧಿಗಳ ಬಗ್ಗೆ ಯಾವುದೇ ಪ್ರಶ್ನೆ ಇದ್ದರೆ ಇಲ್ಲಿ ಕೇಳಬಹುದು."
                        ))
                    else:
                        _send(token, chat_id, (
                            f"Welcome back, {patient['name']}!\n\n"
                            f"Your recovery status:\n"
                            f"[+] Condition: {patient['condition']}\n"
                            f"[R] Prescribed Meds: {patient['meds']}\n\n"
                            "Feel free to ask any question about your diet, medications, or recovery."
                        ))
                    return

            session.current_step = "awaiting_phone"
            session.auth_attempts = 0  # fresh start
            save_session(session)
            if lang == "kn":
                _send(token, chat_id, (
                    f"ಆರೋಗ್ಯ ಬಂಧುಗೆ ಸ್ವಾಗತ, {first_name}! [+]\n\n"
                    "ನಿಮ್ಮ ವೈದ್ಯಕೀಯ ವರದಿ ಮತ್ತು ಔಷಧಿಗಳನ್ನು ಪಡೆಯಲು, ದಯವಿಟ್ಟು ನಿಮ್ಮ ನೋಂದಾಯಿತ ಫೋನ್ ಸಂಖ್ಯೆಯನ್ನು ನಮೂದಿಸಿ:\n"
                    "ಉದಾಹರಣೆಗೆ: +91XXXXXXXXXX"
                ))
            else:
                _send(token, chat_id, (
                    f"Welcome to Aarogya Bandhu, {first_name}! [+]\n\n"
                    "To load your medical report and prescriptions, please enter your "
                    "registered phone number:\n"
                    "Example: +91XXXXXXXXXX"
                ))
            return
        if cmd == "/help":
            if lang == "kn":
                _send(token, chat_id, (
                    "ಸಹಾಯ ಮಾರ್ಗದರ್ಶಿ [+]:\n"
                    "/start — ಲಾಗಿನ್ / ಪುನಃ ಪ್ರಾರಂಭಿಸಿ\n"
                    "/status — ನಿಮ್ಮ ರೋಗಿ ಮಾಹಿತಿ\n"
                    "/diet — ಆಹಾರ ಪದ್ಧತಿ ವಿವರ ನವೀಕರಿಸಿ\n"
                    "/connect_device — ಫಿಟ್‌ನೆಸ್ ಸಾಧನ ಸಂಪರ್ಕಿಸಿ\n"
                    "/staff — ಸಿಬ್ಬಂದಿ ಕೋಡ್ ಪರಿಶೀಲಿಸಿ\n"
                    "/reset — ಲಾಗೌಟ್ ಮಾಡಿ ಪುನಃ ನಮೂದಿಸಿ\n\n"
                    "ಯಾವುದೇ ಪ್ರಶ್ನೆಯನ್ನು ಕನ್ನಡ ಅಥವಾ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ನೇರವಾಗಿ ಟೈಪ್ ಮಾಡಿ!"
                ))
            else:
                _send(token, chat_id, (
                    "Help Guide [+]:\n"
                    "/start — Sign in or restart\n"
                    "/status — View patient summary\n"
                    "/diet — Update diet preferences\n"
                    "/connect_device — Connect smart fitness device\n"
                    "/staff — Enter staff access code\n"
                    "/reset — Reset authentication session\n"
                    "/cancel — Cancel the current operation\n\n"
                    "Or just type any question in English or Kannada!"
                ))
            return

        if cmd == "/staff":
            if not settings.TELEGRAM_STAFF_CODE:
                _send(token, chat_id, "[X] Staff access not configured on this deployment. Ask an admin to set TELEGRAM_STAFF_CODE.")
                return
            _send(token, chat_id, "[K] Enter staff access code:")
            _pending_staff[telegram_id] = True
            return

        if cmd == "/reset":
            reset_session(session)
            _send(token, chat_id, "Session reset. Send /start to sign in again.")
            return

        if cmd == "/lang":
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or parts[1].strip().lower() not in ("en", "kn", "english", "kannada"):
                _send(token, chat_id, (
                    "Usage: /lang en  or  /lang kn\n"
                    "Current language: " + (lang or "en") + "\n"
                    "Your language is sticky — it only changes after 3 messages in a different language, or with this command."
                ))
                return
            new_lang = "kn" if parts[1].strip().lower() in ("kn", "kannada") else "en"
            session.preferred_lang = new_lang
            session.lang_streak = 0
            save_session(session)
            if new_lang == "kn":
                _send(token, chat_id, "[OK] ಭಾಷೆ ಕನ್ನಡಕ್ಕೆ ಬದಲಾಯಿಸಲಾಗಿದೆ. ಈಗ ನಾನು ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸುತ್ತೇನೆ.")
            else:
                _send(token, chat_id, "[OK] Language switched to English. I'll reply in English from now on.")
            return

        if cmd == "/diet":
            session.current_step = "collecting_diet"
            save_session(session)
            if lang == "kn":
                _send(token, chat_id, "[=] ದಯವಿಟ್ಟು ನಿಮ್ಮ ಆಹಾರ ಪದ್ಧತಿ ಅಥವಾ ಆಹಾರ ನಿಯಮಗಳನ್ನು ಟೈಪ್ ಮಾಡಿ (ಉದಾ: ಸಸ್ಯಾಹಾರಿ, ಶಕ್ಕರೆ ಕಾಯಿಲೆ ಆಹಾರ, ಕಡಿಮೆ ಉಪ್ಪು):")
            else:
                _send(token, chat_id, "[=] Please share your diet preferences or restrictions (e.g., Vegetarian, Diabetic diet, Low salt):")
            return

        if cmd == "/status":
            if not session.verified or not session.patient_id:
                _send(token, chat_id, "Please share your registered phone number first.")
                return
            patient = get_patient_report_by_id(session.patient_id)
            if not patient:
                _send(token, chat_id, "No patient found.")
                return
            _send(token, chat_id, (
                f"[#] Patient Summary:\n"
                f"Name: {patient['name']} ({patient['age']}y/{patient['sex']})\n"
                f"Condition: {patient['condition']}\n"
                f"Protocol: {patient['protocol']}\n"
                f"Ward: {patient['ward']}\n"
                f"Discharge: {patient['discharge_date']}\n"
                f"\n[R] Prescribed: {patient['meds']}\n"
                f"[=] Diet: {session.diet_info or 'Not recorded'}\n"
                f"[+] Medication: {session.medication_info or 'Not recorded'}\n"
                f"[?] Feeling: {session.feeling_info or 'Not recorded'}"
            ))
            return

        if cmd == "/medication":
            if not session.verified:
                _send(token, chat_id, "Please verify your phone number first.")
                return
            session.current_step = "collecting_medication"
            save_session(session)
            if lang == "kn":
                _send(token, chat_id, ("[+] ನಿಮ್ಮ ಔಷಧಿ ಸೇವನೆಯ ಮಾಹಿತಿಯನ್ನು "
                                       "ತಿಳಿಸಿ (ಉದಾ: 'ಮಧ್ಯಾಹ್ನ ಒಂದು, ರಾತ್ರಿ ಒಂದు'):"))
            else:
                _send(token, chat_id, ("[+] Share your medication consumption "
                                       "(e.g., 'one at noon, one at night'):"))
            return

        if cmd == "/feeling":
            if not session.verified:
                _send(token, chat_id, "Please verify your phone number first.")
                return
            session.current_step = "collecting_feeling"
            save_session(session)
            if lang == "kn":
                _send(token, chat_id, ("[?] ನಿಮ್ಮ ಆರೋಗ್ಯ ಸ್ಥಿತಿ ಹೇಗಿದೆ ಎಂದು ತಿಳಿಸಿ "
                                       "(ಉದಾ: 'ಉತ್ತಮ', 'ಸ್ವಲ್ಪ ನೋವು'):"))
            else:
                _send(token, chat_id, ("[?] How are you feeling today? "
                                       "(e.g., 'better', 'some pain', 'fever'):"))
            return

        if cmd == "/reports":
            if not session.verified or not session.patient_id:
                _send(token, chat_id, "Please verify your phone number first.")
                return
            reports = get_patient_reports(session.patient_id)
            if not reports:
                if lang == "kn":
                    _send(token, chat_id, "[R] ನಿಮ್ಮ ಯಾವುದೇ ವೈದ್ಯಕೀಯ ವರದಿಗಳು ಇನ್ನೂ ಅಪ್‌ಲೋಡ್ ಆಗಿಲ್ಲ.")
                else:
                    _send(token, chat_id, "[R] No medical reports uploaded yet.")
                return
            # Telegram message limit is 4096 chars. Show top 5 with extracted
            # values so the patient actually sees their lab numbers.
            lines = ["[R] Your Medical Reports (most recent first):\n"]
            type_labels = {
                "lab_report": "Lab Report",
                "discharge_summary": "Discharge Summary",
                "prescription": "Prescription",
                "other": "Other",
            }
            for idx, r in enumerate(reports[:5], 1):
                t = type_labels.get(r["report_type"], r["report_type"])
                when = r["uploaded_at"][:10] if r["uploaded_at"] else "—"
                lines.append(f"{idx}. [{t}] {r['filename']} — {when}")
                if r.get("extracted"):
                    # Show up to 6 key-value pairs from the extraction
                    for k, v in list(r["extracted"].items())[:6]:
                        v_str = str(v)[:80]
                        lines.append(f"   • {k}: {v_str}")
            if len(reports) > 5:
                lines.append(f"\n… and {len(reports) - 5} more. Ask your nurse for full access.")
            if settings.PUBLIC_BASE_URL:
                lines.append(f"\nFull records: {settings.PUBLIC_BASE_URL}/staff/patient-reports")
            _send(token, chat_id, "\n".join(lines))
            return

        if cmd.startswith("/link"):
            # /link <username> — bind this Telegram to a doctor/nurse account
            # so they get direct DM alerts for severe patients.
            from app.models import User
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                _send(token, chat_id, (
                    "Usage: /link <username>\n"
                    "Example: /link dr.priya\n"
                    "Your Telegram account will receive direct alerts for severe patients."
                ))
                return
            username = parts[1].strip().lstrip("@").lower()
            s = SessionLocal()
            try:
                u = s.query(User).filter(
                    User.username == username,
                    User.hospital_code == settings.HOSPITAL_CODE,
                ).first()
                if not u:
                    _send(token, chat_id, f"[X] User '@{username}' not found. Ask your admin to create the account first.")
                    return
                if u.telegram_id and u.telegram_id != telegram_id:
                    _send(token, chat_id, "[X] This account is already linked to a different Telegram. Ask admin to unlink.")
                    return
                u.telegram_id = telegram_id
                s.commit()
                _send(token, chat_id, (
                    f"[OK] Telegram linked to @{u.username} ({u.role}, {u.display_name}).\n\n"
                    f"You will now receive direct alerts for severe patients."
                ))
            finally:
                s.close()
            return

        if cmd.startswith("/reply"):
            # /reply <esc_id_short> <note> — doctor (or nurse) replies to a
            # patient escalation via Telegram. Resolves the escalation and
            # DMs the patient. Requires the sender to be a linked doctor/nurse.
            from app.models import User, Escalation
            # Verify sender is a linked medical staff
            s = SessionLocal()
            try:
                u = s.query(User).filter(
                    User.telegram_id == telegram_id,
                    User.hospital_code == settings.HOSPITAL_CODE,
                ).first()
            finally:
                s.close()
            if not u or u.role not in ("doctor", "nurse", "admin"):
                _send(token, chat_id, "[X] /reply is for medical staff only. Link your account first with /link <username>.")
                return
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                _send(token, chat_id, (
                    "Usage: /reply <esc_id_8chars> <note to patient>\n"
                    "Example: /reply a1b2c3d4 Please continue antibiotics for 3 more days."
                ))
                return
            short_id = parts[1].strip()
            note = parts[2].strip()
            s = SessionLocal()
            try:
                # Match by prefix (esc_id[:8] is what's shown to the doctor)
                x = s.query(Escalation).filter(
                    Escalation.id.like(short_id + "%"),
                    Escalation.hospital_code == settings.HOSPITAL_CODE,
                ).first()
                if not x:
                    _send(token, chat_id, f"[X] No escalation found starting with '{short_id}'.")
                    return
                # Update + resolve
                x.acked_by = x.acked_by or u.id
                x.acked_at = x.acked_at or now_utc()
                x.resolved_by = u.id
                x.resolved_at = now_utc()
                x.status = "resolved"
                x.resolution_note = f"[via Telegram by {u.display_name}] {note}"
                s.commit()
                # DM the patient (best-effort)
                try:
                    notify_patient_escalation_resolved(x.id, note)
                except Exception:
                    pass
                _send(token, chat_id, (
                    f"[OK] Escalation {short_id} resolved.\n\n"
                    f"Patient has been notified:\n\"{note[:200]}\""
                ))
            finally:
                s.close()
            return

        if cmd == "/connect_device":
            if not session.verified or not session.phone:
                _send(token, chat_id, "Please verify your phone number first.")
                return
            oauth_url = f"{settings.PUBLIC_BASE_URL or 'http://localhost:8000'}/api/health/fit/authorize?tgid={telegram_id}"
            _send(token, chat_id, (
                "[M] Connect Google Fit / Smart Watch:\n"
                f"1. Tap link to authorize: {oauth_url}\n"
                "2. Complete Google login and return to chat.\n"
                "3. You'll get a confirmation here once linked."
            ))
            return

        if cmd == "/skip":
            session.current_step = "active"
            save_session(session)
            _send(token, chat_id, "Setup complete! You can now chat anytime.")
            return

    # ── Imperative state machine ──────────────────────────────────────────

    # Step: multi-patient family member selection
    if session.current_step == "selecting_family_member" and telegram_id in _pending_family_selection:
        matches = _pending_family_selection[telegram_id]
        try:
            choice = int(text.strip())
            if 1 <= choice <= len(matches):
                selected = matches[choice - 1]
                session.patient_id = selected["patient_id"]
                session.verified = True
                session.current_step = "collecting_diet"
                save_session(session)
                del _pending_family_selection[telegram_id]

                if lang == "kn":
                    _send(token, chat_id, (
                        f"[OK] {selected['name']} ಅವರ ಖಾತೆಗೆ ಸಂಪರ್ಕಿಸಲಾಗಿದೆ!\n\n"
                        "ಉತ್ತಮ ಚೇತರಿಕೆಗೆ ನಿಮ್ಮ ದಿನನಿತ್ಯದ ಆಹಾರ ಪದ್ಧತಿಯನ್ನು ತಿಳಿಸಿ (ಉದಾ: ಸಸ್ಯಾಹಾರಿ, ಮಧುಮೇಹ ಆಹಾರ, ಕಡಿಮೆ ಖಾರ):"
                    ))
                else:
                    _send(token, chat_id, (
                        f"[OK] Linked to {selected['name']}!\n\n"
                        "Please share your daily diet preferences or restrictions (e.g., Vegetarian, Diabetic diet, Low salt):"
                    ))
                return
            else:
                _send(token, chat_id, f"Please enter a valid choice between 1 and {len(matches)}.")
                return
        except ValueError:
            _send(token, chat_id, f"Please enter a valid number (1-{len(matches)}).")
            return

    # Step: awaiting phone (or implicit phone-in-text entry)
    if session.current_step == "awaiting_phone" or (
            not session.verified and re.search(r"\+?\d{10,12}", text)):
        phone = _normalise_phone(text)
        if phone:
            # Persist + check lockout BEFORE we send a misleading "no match"
            # message that could be used to enumerate numbers.
            session.auth_attempts += 1
            if session.auth_attempts > MAX_AUTH_ATTEMPTS:
                reset_session(session)
                _send(token, chat_id,
                      "[X] Too many failed attempts. Session reset. Send /start to try again.")
                return

            matches = lookup_patients_by_phone(phone)
            if len(matches) == 0:
                save_session(session)
                if lang == "kn":
                    _send(token, chat_id, f"[X] ಈ ಫೋನ್ ಸಂಖ್ಯೆಗೆ ಆಸ್ಪತ್ರೆ ದಾಖಲೆ ಕಂಡುಬಂದಿಲ್ಲ. ದಯವಿಟ್ಟು ನೋಂದಾಯಿತ ಸಂಖ್ಯೆಯನ್ನು ನಮೂದಿಸಿ. ({session.auth_attempts}/{MAX_AUTH_ATTEMPTS} ಪ್ರಯತ್ನಗಳು)")
                else:
                    _send(token, chat_id, f"[X] No patient record found for this phone number. Please check and try again. ({session.auth_attempts}/{MAX_AUTH_ATTEMPTS} attempts)")
                return
            elif len(matches) == 1:
                selected = matches[0]
                session.phone = phone
                session.patient_id = selected["patient_id"]
                session.verified = True
                session.auth_attempts = 0
                session.current_step = "collecting_diet"
                save_session(session)

                if lang == "kn":
                    _send(token, chat_id, (
                        f"[OK] ಪರಿಶೀಲಿಸಲಾಗಿದೆ! ನಮಸ್ಕಾರ {selected['name']}.\n\n"
                        "ಉತ್ತಮ ಚೇತರಿಕೆಗೆ ನಿಮ್ಮ ದಿನನಿತ್ಯದ ಆಹಾರ ಪದ್ಧತಿಯನ್ನು ತಿಳಿಸಿ (ಉದಾ: ಸಸ್ಯಾಹಾರಿ, ಡಯಾಬಿಟಿಸ್, ಕಡಿಮೆ ಉಪ್ಪು):"
                    ))
                else:
                    _send(token, chat_id, (
                        f"[OK] Verified! Welcome, {selected['name']}.\n\n"
                        "Please share your daily diet preferences or restrictions (e.g., Vegetarian, Diabetic diet, Low salt):"
                    ))
                return
            else:
                # multiple family members
                _pending_family_selection[telegram_id] = matches
                _evict_family_selection_if_full()
                session.phone = phone
                session.current_step = "selecting_family_member"
                session.auth_attempts = 0
                save_session(session)

                lines = []
                if lang == "kn":
                    lines.append("[M] ಈ ಫೋನ್ ಸಂಖ್ಯೆಗೆ 1 ಕ್ಕಿಂತ ಹೆಚ್ಚು ರೋಗಿಗಳು ನೋಂದಾಯಿಸಲ್ಪಟ್ಟಿದ್ದಾರೆ. ಯಾರು ಚಾಟ್ ಮಾಡುತ್ತಿದ್ದಾರೆಂದು ಆಯ್ಕೆ ಮಾಡಿ:\n")
                    for idx, p_item in enumerate(matches, 1):
                        lines.append(f"{idx}. {p_item['name']} (ವಯಸ್ಸು {p_item['age'] or '—'})")
                    lines.append("\nಸಂಖ್ಯೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ (ಉದಾ: 1):")
                else:
                    lines.append("[M] Multiple patients found for this mobile number. Please select who is using the chat:\n")
                    for idx, p_item in enumerate(matches, 1):
                        lines.append(f"{idx}. {p_item['name']} (Age {p_item['age'] or '—'})")
                    lines.append("\nType the option number (e.g., 1):")
                _send(token, chat_id, "\n".join(lines))
                return

    # Step: collecting diet
    if session.current_step == "collecting_diet":
        session.diet_info = text.strip()
        session.current_step = "collecting_medication"
        save_session(session)

        if lang == "kn":
            _send(token, chat_id, (
                "[OK] ಆಹಾರದ ವಿವರಗಳನ್ನು ಉಳಿಸಲಾಗಿದೆ! [=]\n\n"
                "[+] ಔಷಧಿ ಸೇವನೆ: ನಿಮ್ಮ ನೇಮಕ್ಕಾದ ಔಷಧಿಗಳನ್ನು ಎಷ್ಟು ಬಾರಿ / ಯಾವಾಗ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಿ?\n"
                "ಉದಾ: 'ಮಧ್ಯಾಹ್ನ 12 ಗಂಟೆಗೆ ಒಂದು ಆ್ಯಂಟಿಬಯೋಟಿಕ್, ರಾತ್ರಿ 9 ಗಂಟೆಗೆ ಒಂದು'"
            ))
        else:
            _send(token, chat_id, (
                "[OK] Diet info saved! [=]\n\n"
                "[+] Medication consumption: How often / when are you taking your "
                "prescribed medicines?\n"
                "Example: 'One antibiotic at 12pm, one at 9pm'"
            ))
        return

    # Step: collecting medication
    if session.current_step == "collecting_medication":
        session.medication_info = text.strip()
        session.current_step = "collecting_feeling"
        save_session(session)

        if lang == "kn":
            _send(token, chat_id, (
                "[OK] ಔಷಧಿ ಮಾಹಿತಿ ಉಳಿಸಲಾಗಿದೆ! [+]\n\n"
                "[?] ನಿಮ್ಮ ಆರೋಗ್ಯ ಸ್ಥಿತಿ ಹೇಗಿದೆ?\n"
                "ಉದಾ: 'ಉತ್ತಮ ಚೇತರಿಕೆ', 'ಸ್ವಲ್ಪ ನೋವು', 'ಜ್ವರ ಕಡಿಮೆಯಾಗಿದೆ'"
            ))
        else:
            _send(token, chat_id, (
                "[OK] Medication info saved! [+]\n\n"
                "[?] How are you feeling today?\n"
                "Example: 'Recovering well', 'Some pain', 'Fever has reduced'"
            ))
        return

    # Step: collecting feeling
    if session.current_step == "collecting_feeling":
        session.feeling_info = text.strip()
        session.current_step = "active"
        save_session(session)

        if lang == "kn":
            _send(token, chat_id, (
                "[OK] ಆರೋಗ್ಯ ವಿವರ ಉಳಿಸಲಾಗಿದೆ! [?]\n\n"
                "ನಿಮ್ಮ ಚೇತರಿಕೆ, ಔಷಧಿ ಮತ್ತು ದಿನನಿತ್ಯದ ಸಲಹೆಗಳನ್ನು ಈಗ ಕೇಳಬಹುದು.\n"
                "ತುರ್ತು ಸಹಾಯಕ್ಕಾಗಿ ನೇರವಾಗಿ 'sos' ಎಂದು ಟೈಪ್ ಮಾಡಿ."
            ))
        else:
            _send(token, chat_id, (
                "[OK] Feeling info saved! [?]\n\n"
                "Setup is complete. Feel free to ask any question about your recovery or medications.\n"
                "For emergencies, type 'sos' anytime."
            ))
        return

    # ── Active chat & RAG ─────────────────────────────────────────────────
    patient_ctx = None
    if session.verified and session.patient_id:
        patient_ctx = get_patient_report_by_id(session.patient_id)
        if patient_ctx and session.diet_info:
            patient_ctx["diet_info"] = session.diet_info

    # Persist a marker that this user is in active mode (helps /reset
    # clear it correctly and gives the dashboard a last-active timestamp).
    if session.current_step != "active" and patient_ctx:
        session.current_step = "active"
        save_session(session)

    if patient_ctx:
        if _detect_sos(text):
            esc_id = _create_alert(patient_ctx, text, "critical")
            if esc_id:
                _send_alert_to_group(patient_ctx, text, "critical", esc_id)
                _send_alert_to_doctors(patient_ctx, text, "critical", esc_id)
                if lang == "kn":
                    _send(token, chat_id, (
                        "[!] SOS ಸ್ವೀಕರಿಸಲಾಗಿದೆ — ನಿಮ್ಮ ತುರ್ತು ಸಹಾಯ ವಿನಂತಿಯನ್ನು ವೈದ್ಯರ ಡ್ಯಾಶ್‌ಬೋರ್ಡ್‌ಗೆ ತಕ್ಷಣ ರವಾನಿಸಲಾಗಿದೆ.\n"
                        "ಜೀವಕ್ಕೆ ಅಪಾಯಕಾರಿ ಸಂದರ್ಭದಲ್ಲಿ, 104 ಅಥವಾ 108 ಗೆ ಕರೆ ಮಾಡಿ!"
                    ))
                else:
                    _send(token, chat_id, (
                        "[!] SOS RECEIVED — Your emergency alert has been sent to the doctor's dashboard immediately.\n"
                        "For life-threatening emergencies, call 104 or 108 NOW!"
                    ))
                return

        severity = _detect_symptoms(text)
        if severity in ("critical", "high"):
            esc_id = _create_alert(patient_ctx, text, severity)
            if esc_id:
                _send_alert_to_group(patient_ctx, text, severity, esc_id)
                _send_alert_to_doctors(patient_ctx, text, severity, esc_id)
                if lang == "kn":
                    _send(token, chat_id, (
                        f"[!] ನಿಮ್ಮ ಲಕ್ಷಣವನ್ನು ({severity.upper()}) ವೈದ್ಯರ ಡ್ಯಾಶ್‌ಬೋರ್ಡ್‌ಗೆ ವರದಿ ಮಾಡಲಾಗಿದೆ.\n"
                        "ವೈದ್ಯರು ಪರಿಶೀಲಿಸುತ್ತಿದ್ದಾರೆ. ತುರ್ತು ಸಂದರ್ಭದಲ್ಲಿ 104/108 ಗೆ ಕರೆ ಮಾಡಿ."
                    ))
                else:
                    _send(token, chat_id, (
                        f"[!] Your symptom ({severity.upper()}) has been forwarded to the doctor's dashboard.\n"
                        "The medical team is reviewing your report. For emergencies call 104 or 108."
                    ))
                context = retrieve(text, patient_ctx)
                rag_resp = ask_llm(text, context, patient_ctx, is_staff=session.staff, lang=lang)
                _send(token, chat_id, rag_resp)
                return

    context = retrieve(text, patient_ctx)
    response = ask_llm(text, context, patient_ctx, is_staff=session.staff, lang=lang)
    _send(token, chat_id, response)


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
                            loop = asyncio.get_running_loop()
                            try:
                                await loop.run_in_executor(None, _handle_message, token, msg)
                            except Exception as e:
                                # Never let one bad message kill the polling
                                # loop. Log and continue.
                                log.exception("unhandled error in _handle_message: %s", e)
                                try:
                                    _send(token, msg["chat"]["id"],
                                          "[X] Something went wrong on our side. Please try again.")
                                except Exception:
                                    pass
                else:
                    log.warning("getUpdates %s: %s", r.status_code, r.text[:100])
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                log.info("telegram polling stopped")
                break
            except Exception as e:
                log.warning("poll error: %s", e)
                await asyncio.sleep(5)


def notify_google_fit_linked(telegram_id: int) -> None:
    """Called by the health-fit OAuth callback after a successful link.

    T12 follow-up: this was previously dead code (the OAuth callback
    updated the DB but never told the user). Now it:
    1. Marks the session's `google_fit_consent=True` (persisted)
    2. Sends a Telegram confirmation
    3. Falls back to logging if Telegram isn't configured.
    """
    s = get_session(telegram_id)
    s.google_fit_consent = True
    save_session(s)
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        log.info("google_fit linked for telegram_id=%s (no token to notify)", telegram_id)
        return
    s_db = SessionLocal()
    try:
        chat_id_row = s_db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if not chat_id_row:
            return
        _send(token, chat_id_row.telegram_id,
              "[OK] Google Fit linked. Your fitness data will start syncing within 24h.")
    finally:
        s_db.close()
