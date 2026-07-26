"""Telegram bot session management: OTP, verification, rate limiting."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class Session:
    telegram_id: int
    phone: str | None = None
    verified: bool = False
    staff: bool = False
    otp: str | None = None
    otp_expires: float = 0.0
    last_message: float = 0.0
    message_count: int = 0
    window_start: float = 0.0
    verified_at: float = 0.0


# in-memory store (resets on restart — fine for hackathon)
_sessions: dict[int, Session] = {}

# rate limits: (messages_per_hour, burst_per_minute)
LIMITS = {
    "anonymous": (10, 5),
    "patient": (30, 10),
    "staff": (60, 20),
}

OTP_TTL = 300  # 5 minutes


def get_session(telegram_id: int) -> Session:
    if telegram_id not in _sessions:
        _sessions[telegram_id] = Session(telegram_id=telegram_id)
    return _sessions[telegram_id]


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
        session.verified_at = time.time()
        return True
    return False


def verify_staff(session: Session, code: str) -> bool:
    if code.strip() == settings.TELEGRAM_STAFF_CODE:
        session.staff = True
        session.verified_at = time.time()
        return True
    return False


def check_rate_limit(session: Session) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    now = time.time()
    role = "staff" if session.staff else ("patient" if session.verified else "anonymous")
    limit, burst = LIMITS[role]

    # rolling 1-hour window
    if now - session.window_start > 3600:
        session.message_count = 0
        session.window_start = now

    session.message_count += 1

    if session.message_count > limit:
        wait = int(3600 - (now - session.window_start)) + 1
        return False, wait

    # burst check: max N messages per minute
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
