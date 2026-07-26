"""Telegram red-flag alerts (docs/04 §6). Plain httpx, no SDK.

Fires on escalation creation only (engine red hook). Failures are swallowed so a
Telegram outage never breaks a call. No-op when TELEGRAM_BOT_TOKEN is unset.
"""
import logging

import httpx

from app.config import settings
from app.models import Enrollment, Patient

log = logging.getLogger("notify")


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return "••••"
    return f"{phone[:6]}•••••{phone[-3:]}"


def _message(esc, patient_name: str, phone: str) -> str:
    reasons = esc.reasons or "[]"
    return (
        f"🔴 RED FLAG — {settings.HOSPITAL_NAME}\n"
        f"Patient: {patient_name} (enrollment {esc.enrollment_id[:8]})\n"
        f"Reasons: {reasons}\n"
        f"Caregiver: {_mask(phone)}\n"
        f"Escalation page: {settings.PUBLIC_BASE_URL}/escalations\n"
        f"Call caregiver: tel:{phone}"
    ).replace("[", "(").replace("]", ")")


def telegram_red(esc) -> str | None:
    """Synchronous (called from the engine hook, on a worker thread). Returns msg or None."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return None
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        en = s.query(Enrollment).filter(Enrollment.id == esc.enrollment_id).first()
        if not en:
            return None
        p = s.query(Patient).filter(Patient.id == en.patient_id).first()
        if not p:
            return None
        msg = _message(esc, p.name, p.caregiver_phone)
    finally:
        s.close()
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": settings.TELEGRAM_CHAT_ID,
                  "text": msg, "disable_web_page_preview": True},
            timeout=5.0,
        )
        if r.status_code != 200:
            log.warning("telegram sendMessage %s: %s", r.status_code, r.text[:200])
        return msg if r.status_code == 200 else None
    except Exception as ex:  # never raise from a hook
        log.warning("telegram failed: %s", ex)
        return None