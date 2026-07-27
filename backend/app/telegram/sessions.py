"""Telegram bot session management: persistent DB sessions, OTP,
multi-patient phone lookup, rate limiting.

Schema notes (T12 follow-up): every field the imperative `bot.py`
state machine needs to survive a restart is persisted in the
`telegram_sessions` table. In-memory `_sessions` is just a per-process
cache rehydrated from the DB.
"""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field

from app.config import settings
from app.db import SessionLocal, now_utc
from app.models import Enrollment, EnrollmentMed, Patient, TelegramSession

MAX_AUTH_ATTEMPTS = 5  # before lockout


@dataclass
class Session:
    telegram_id: int
    phone: str | None = None
    patient_id: str | None = None
    verified: bool = False
    staff: bool = False
    admin: bool = False           # verified admin via contact share
    preferred_lang: str = "en"
    diet_info: str | None = None
    google_fit_consent: bool = False
    current_step: str | None = None
    # auth-failure counter (imperative flow). Persisted in DB so
    # /reset → /start cycle actually clears it.
    auth_attempts: int = 0
    # in-memory only (rate limiter state — a restart just gives a
    # full window back, acceptable for a bot).
    otp: str | None = None
    otp_expires: float = 0.0
    last_message: float = 0.0
    message_count: int = 0
    window_start: float = 0.0


_sessions: dict[int, Session] = {}

# Bound the in-memory cache to avoid leaking across restarts with many
# distinct telegram_ids (process restart flushes this; the bound is just
# for very long-lived processes).
_SESSION_CACHE_MAX = 10000


def _evict_cache_if_full() -> None:
    if len(_sessions) <= _SESSION_CACHE_MAX:
        return
    # Cap the cache at _SESSION_CACHE_MAX by dropping the oldest
    # entries (insertion order is FIFO — older sessions are likelier
    # to be stale).
    target = max(0, _SESSION_CACHE_MAX // 2)  # leave room for growth
    while len(_sessions) > target:
        oldest = next(iter(_sessions))
        _sessions.pop(oldest, None)


LIMITS = {
    "anonymous": (15, 8),
    "patient": (60, 20),
    "staff": (100, 30),
}

OTP_TTL = 300  # 5 minutes


def get_session(telegram_id: int) -> Session:
    if telegram_id in _sessions:
        return _sessions[telegram_id]

    s_db = SessionLocal()
    try:
        ts = s_db.query(TelegramSession).filter(
            TelegramSession.telegram_id == telegram_id).first()
        if ts:
            session = Session(
                telegram_id=telegram_id,
                phone=ts.phone,
                patient_id=ts.patient_id,
                verified=bool(ts.is_verified),
                staff=bool(ts.is_staff),
                admin=bool(getattr(ts, "is_admin", 0) or 0),
                preferred_lang=ts.preferred_lang or "en",
                diet_info=ts.diet_info,
                google_fit_consent=bool(ts.google_fit_consent),
                current_step=ts.current_step,
                auth_attempts=getattr(ts, "auth_attempts", 0) or 0,
            )
        else:
            ts = TelegramSession(
                id=uuid.uuid4().hex,
                telegram_id=telegram_id,
                preferred_lang="en",
                is_verified=0,
                is_staff=0,
                is_admin=0,
                auth_attempts=0,
                updated_at=now_utc(),
            )
            s_db.add(ts)
            s_db.commit()
            session = Session(telegram_id=telegram_id)
        _sessions[telegram_id] = session
        _evict_cache_if_full()
        return session
    except Exception:
        # DB unreachable — fall back to in-memory only.
        return Session(telegram_id=telegram_id)
    finally:
        s_db.close()


def save_session(session: Session) -> None:
    """Persist the session's authoritative fields to the DB.

    Auth state, attempts counter, language, staff/admin flags, and
    current_step are all authoritative. The in-memory rate-limit fields
    (last_message, window_start, message_count) are intentionally NOT
    persisted — a restart just gives the user a fresh window.
    """
    s_db = SessionLocal()
    try:
        ts = s_db.query(TelegramSession).filter(
            TelegramSession.telegram_id == session.telegram_id).first()
        if not ts:
            ts = TelegramSession(id=uuid.uuid4().hex,
                                 telegram_id=session.telegram_id)
            s_db.add(ts)
        ts.phone = session.phone
        ts.patient_id = session.patient_id
        ts.is_verified = 1 if session.verified else 0
        ts.is_staff = 1 if session.staff else 0
        ts.is_admin = 1 if session.admin else 0
        ts.auth_attempts = session.auth_attempts
        ts.preferred_lang = session.preferred_lang
        ts.diet_info = session.diet_info
        ts.google_fit_consent = 1 if session.google_fit_consent else 0
        ts.current_step = session.current_step
        ts.updated_at = now_utc()
        s_db.commit()
    except Exception:
        s_db.rollback()
    finally:
        s_db.close()


def reset_session(session: Session) -> None:
    """Full reset: clears identity, attempts, language, and admin/staff flags.

    Used by the /reset command and by the auth lockout.
    """
    session.phone = None
    session.patient_id = None
    session.verified = False
    session.staff = False
    session.admin = False
    session.auth_attempts = 0
    session.diet_info = None
    session.google_fit_consent = False
    session.current_step = "awaiting_phone"
    session.preferred_lang = "en"
    save_session(session)


def lookup_patients_by_phone(phone: str) -> list[dict]:
    """Find all patient records sharing the given caregiver phone number."""
    s = SessionLocal()
    try:
        patients = s.query(Patient).filter(Patient.caregiver_phone == phone).all()
        results = []
        for p in patients:
            en = s.query(Enrollment).filter(Enrollment.patient_id == p.id).first()
            meds = []
            if en:
                for m in s.query(EnrollmentMed).filter(
                        EnrollmentMed.enrollment_id == en.id).all():
                    meds.append(f"{m.med_name} ({m.doses_per_day}x/day)")
            results.append({
                "patient_id": p.id,
                "name": p.name,
                "age": p.age,
                "sex": p.sex,
                "phone": p.caregiver_phone,
                "condition": en.condition_label if en else "General Recovery",
                "protocol": en.protocol_id if en else "General",
                "ward": en.ward if en else "General",
                "discharge_date": en.discharge_date if en else "N/A",
                "meds": ", ".join(meds) if meds else "None prescribed",
            })
        return results
    finally:
        s.close()


def get_patient_report_by_id(patient_id: str) -> dict | None:
    """Lookup specific patient details by patient_id."""
    s = SessionLocal()
    try:
        p = s.query(Patient).filter(Patient.id == patient_id).first()
        if not p:
            return None
        en = s.query(Enrollment).filter(Enrollment.patient_id == p.id).first()
        meds = []
        if en:
            for m in s.query(EnrollmentMed).filter(
                    EnrollmentMed.enrollment_id == en.id).all():
                meds.append(f"{m.med_name} ({m.doses_per_day}x/day)")
        return {
            "patient_id": p.id,
            "name": p.name,
            "age": p.age,
            "sex": p.sex,
            "phone": p.caregiver_phone,
            "condition": en.condition_label if en else "General Recovery",
            "protocol": en.protocol_id if en else "General",
            "ward": en.ward if en else "General",
            "discharge_date": en.discharge_date if en else "N/A",
            "meds": ", ".join(meds) if meds else "None prescribed",
        }
    finally:
        s.close()


def generate_otp() -> str:
    return f"{random.randint(100000, 999999)}"


def set_otp(session: Session) -> str:
    otp = generate_otp()
    session.otp = otp
    session.otp_expires = time.time() + OTP_TTL
    return otp


def verify_otp(session: Session, attempt: str) -> bool:
    if not session.otp or time.time() > session.otp_expires:
        session.otp = None
        return False
    if session.otp == attempt:
        session.verified = True
        session.otp = None
        save_session(session)
        return True
    return False


def verify_staff(session: Session, code: str) -> bool:
    if not settings.TELEGRAM_STAFF_CODE:
        return False
    if code.strip() == settings.TELEGRAM_STAFF_CODE:
        session.staff = True
        save_session(session)
        return True
    return False


def check_rate_limit(session: Session) -> tuple[bool, int]:
    now = time.time()
    role = "staff" if session.staff else (
        "patient" if session.verified else "anonymous")
    limit, burst = LIMITS[role]

    if now - session.window_start > 3600:
        session.message_count = 0
        session.window_start = now

    session.message_count += 1

    if session.message_count > limit:
        wait = int(3600 - (now - session.window_start)) + 1
        return False, wait

    if now - session.last_message < 60:
        if session.message_count > burst:
            return False, 60 - int(now - session.last_message)

    session.last_message = now
    return True, 0


def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    s = seconds % 60
    return f"{m}m {s}s" if s else f"{m}m"
