"""AMR Active Stewardship (docs/03 §7 — Telegram-based active layer).

Proactive daily medication reminders to verified patients via Telegram.
Pill count check-ins for antibiotic patients. Non-adherence escalation.
Weekly summary to staff group.

No LLM in this path — deterministic messages only.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.db import SessionLocal, now_utc
from app.models import Enrollment, EnrollmentMed, Escalation, FollowupCall, Patient

log = logging.getLogger("amr_steward")

# ── in-memory state (resets on restart — fine for hackathon) ────────────────────

# {enrollment_id: timestamp} — last reminder sent
_reminded_today: dict[str, float] = {}
# {enrollment_id: timestamp} — last pill count request sent
_pill_count_sent: dict[str, float] = {}
# {enrollment_id: int} — pill count response (remaining pills)
_pill_count_response: dict[str, int] = {}
# {enrollment_id: timestamp} — patient confirmed meds taken
_meds_confirmed: dict[str, float] = {}

REMINDER_COOLDOWN = 86400  # 24 hours
PILL_COUNT_DAY = 7  # day_index to ask pill count


# ── helpers ────────────────────────────────────────────────────────────────────

def _send_telegram(chat_id: int, text: str) -> bool:
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10.0,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False


def _find_telegram_id(phone: str) -> int | None:
    """Find Telegram user ID for a phone via the bot's session store."""
    from app.telegram.sessions import _sessions
    for sid, sess in _sessions.items():
        if sess.phone == phone and sess.verified:
            return sid
    return None


def _get_course_end_day(meds: list[EnrollmentMed]) -> int | None:
    """Return the max course_days across antibiotic meds, or None."""
    max_days = None
    for m in meds:
        if m.med_type == "antibiotic" and m.course_days:
            if max_days is None or m.course_days > max_days:
                max_days = m.course_days
    return max_days


def _get_completed_call_count(enrollment_id: str) -> int:
    s = SessionLocal()
    try:
        return s.query(FollowupCall).filter(
            FollowupCall.enrollment_id == enrollment_id,
            FollowupCall.status == "completed",
        ).count()
    finally:
        s.close()


def _get_last_risk(enrollment_id: str) -> str | None:
    s = SessionLocal()
    try:
        call = (s.query(FollowupCall).filter(
            FollowupCall.enrollment_id == enrollment_id,
            FollowupCall.status == "completed",
        ).order_by(FollowupCall.completed_at.desc()).first())
        return call.risk_level if call else None
    finally:
        s.close()


# ── daily reminder job ─────────────────────────────────────────────────────────

def send_daily_reminders() -> int:
    """Send medication reminders to verified patients with antibiotics.
    Returns count of reminders sent.
    """
    s = SessionLocal()
    sent = 0
    try:
        now = time.time()
        enrollments = s.query(Enrollment).filter(
            Enrollment.status == "active",
        ).all()

        for en in enrollments:
            # cooldown check
            last = _reminded_today.get(en.id, 0)
            if now - last < REMINDER_COOLDOWN:
                continue

            # must have antibiotics
            meds = s.query(EnrollmentMed).filter(
                EnrollmentMed.enrollment_id == en.id,
                EnrollmentMed.med_type == "antibiotic",
            ).all()
            if not meds:
                continue

            # course still active?
            course_end = _get_course_end_day(meds)
            if course_end is not None:
                calls_done = _get_completed_call_count(en.id)
                # approximate: each completed call covers ~1 day of the course
                # if all course days have calls completed, course is done
                if calls_done >= course_end:
                    continue

            # patient must have verified phone
            p = s.query(Patient).filter(Patient.id == en.patient_id).first()
            if not p or not p.caregiver_phone:
                continue

            # find Telegram session
            tg_id = _find_telegram_id(p.caregiver_phone)
            if tg_id is None:
                continue

            # send reminder
            med_names = ", ".join(m.med_name for m in meds)
            msg = (
                f"💊 ಔಷಧ ಜ್ಞಾಪನೆ — {settings.HOSPITAL_NAME}\n\n"
                f"ನಮಸ್ಕಾರ {p.name}!\n\n"
                f"ನಿಮ್ಮ ಔಷಧಗಳನ್ನು ಸಮಯಕ್ಕೆ ತೆಗೆದುಕೊಳ್ಳಿ:\n"
                f"• {med_names}\n\n"
                f"ಔಷध ತೆಗೆದುಕೊಂಡಿರುವಿರಾ? /confirm ಕಳುಹಿಸಿ\n"
                f"ಮಾತ್ರೆ ಉಳಿದಿವೆಯೇ? /pills ಕಳುಹಿಸಿ\n\n"
                f"ಸಮಯಕ್ಕೆ ಔಷಧ ತೆಗೆದುಕೊಳ್ಳುವುದು ಗುಣಲಕ್ಷಣಕ್ಕೆ ಸಹಾಯಕ."
            )
            if _send_telegram(tg_id, msg):
                _reminded_today[en.id] = now
                sent += 1
                log.info("sent daily reminder to %s (enrollment %s)", p.name, en.id[:8])

    except Exception as e:
        log.warning("daily reminders failed: %s", e)
    finally:
        s.close()
    return sent


# ── pill count check ───────────────────────────────────────────────────────────

def check_pill_count() -> int:
    """Send pill count check-in for antibiotic patients on day 7.
    Returns count of messages sent.
    """
    s = SessionLocal()
    sent = 0
    try:
        now = time.time()
        enrollments = s.query(Enrollment).filter(
            Enrollment.status == "active",
        ).all()

        for en in enrollments:
            if _pill_count_sent.get(en.id, 0) > 0:
                continue

            # check if day 7 call completed
            day7_call = s.query(FollowupCall).filter(
                FollowupCall.enrollment_id == en.id,
                FollowupCall.day_index == PILL_COUNT_DAY,
                FollowupCall.status == "completed",
            ).first()
            if not day7_call:
                continue

            # must have antibiotics
            meds = s.query(EnrollmentMed).filter(
                EnrollmentMed.enrollment_id == en.id,
                EnrollmentMed.med_type == "antibiotic",
            ).all()
            if not meds:
                continue

            p = s.query(Patient).filter(Patient.id == en.patient_id).first()
            if not p or not p.caregiver_phone:
                continue

            tg_id = _find_telegram_id(p.caregiver_phone)
            if tg_id is None:
                continue

            total_pills = sum((m.course_days or 0) * (m.doses_per_day or 1) for m in meds)
            msg = (
                f"📊 ಮಾತ್ರೆ ಎಣಿಕೆ — {settings.HOSPITAL_NAME}\n\n"
                f"ನಮಸ್ಕಾರ {p.name}!\n\n"
                f"ನಿಮ್ಮ ಚಿಕಿತ್ಸೆ ಅರ್ಧದಷ್ಟು ಆಗಿದೆ.\n"
                f"ಒಟ್ಟು ಮಾತ್ರೆಗಳು: {total_pills}\n\n"
                f"ಉಳಿದ ಮಾತ್ರೆಗಳ ಸಂಖ್ಯೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ (ಉದಾ: 12)\n"
                f"ಅಥವಾ /pills ಕಳುಹಿಸಿ"
            )
            if _send_telegram(tg_id, msg):
                _pill_count_sent[en.id] = now
                sent += 1
                log.info("sent pill count check to %s (enrollment %s)", p.name, en.id[:8])

    except Exception as e:
        log.warning("pill count check failed: %s", e)
    finally:
        s.close()
    return sent


# ── non-adherence escalation ──────────────────────────────────────────────────

def check_non_adherence() -> int:
    """Check for non-adherence (no confirmation within 24h) and create escalations.
    Returns count of escalations created.
    """
    s = SessionLocal()
    escalated = 0
    try:
        now = time.time()
        enrollments = s.query(Enrollment).filter(
            Enrollment.status == "active",
        ).all()

        for en in enrollments:
            last_remind = _reminded_today.get(en.id, 0)
            if last_remind == 0:
                continue

            # if reminder was sent but no confirmation within 24h
            if now - last_remind < REMINDER_COOLDOWN:
                continue

            # already confirmed?
            if en.id in _meds_confirmed:
                confirmed_at = _meds_confirmed[en.id]
                if confirmed_at > last_remind:
                    continue

            # check for existing open escalation
            existing = s.query(Escalation).filter(
                Escalation.enrollment_id == en.id,
                Escalation.status == "open",
            ).first()
            if existing:
                continue

            # create non-adherence escalation
            p = s.query(Patient).filter(Patient.id == en.patient_id).first()
            if not p:
                continue

            esc = Escalation(
                hospital_code=en.hospital_code,
                enrollment_id=en.id,
                call_id=None,
                level="yellow",
                reasons='["AMR steward: no medication confirmation within 24h"]',
                status="open",
                created_at=now_utc(),
            )
            s.add(esc)
            s.commit()
            escalated += 1
            log.info("created non-adherence escalation for %s (enrollment %s)",
                     p.name, en.id[:8])

            # notify staff group
            _notify_staff_non_adherence(p, en, esc)

    except Exception as e:
        log.warning("non-adherence check failed: %s", e)
    finally:
        s.close()
    return escalated


def _notify_staff_non_adherence(patient: Patient, enrollment: Enrollment, esc) -> None:
    chat_id = settings.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    msg = (
        f"🟡 AMR NON-ADHERENCE — {settings.HOSPITAL_NAME}\n"
        f"Patient: {patient.name}\n"
        f"Enrollment: {enrollment.id[:8]}\n"
        f"Condition: {enrollment.condition_label}\n"
        f"Protocol: {enrollment.protocol_id}\n"
        f"No medication confirmation received within 24h.\n"
        f"Dashboard: {settings.PUBLIC_BASE_URL}/escalations"
    )
    _send_telegram(int(chat_id), msg)


# ── patient response handlers ──────────────────────────────────────────────────

def confirm_meds(phone: str) -> str:
    """Patient confirms they took their meds. Returns confirmation message."""
    s = SessionLocal()
    try:
        en = s.query(Enrollment).join(Patient).filter(
            Patient.caregiver_phone == phone,
            Enrollment.status == "active",
        ).first()
        if not en:
            return "No active enrollment found for your phone number."

        _meds_confirmed[en.id] = time.time()
        log.info("meds confirmed for enrollment %s", en.id[:8])
        return (
            "✓ ಧನ್ಯವಾದಗಳು! ಔಷದ ತೆಗೆದುಕೊಂಡಿರುವುದನ್ನು ದೃಢೀಕರಿಸಲಾಗಿದೆ.\n"
            "ಮುಂದುವರಿಯಿರಿ, ಚೆನ್ನಾಗಿ ಗುಣವಾಗುತ್ತದೆ!"
        )
    finally:
        s.close()


def report_pill_count(phone: str, count: int) -> str:
    """Patient reports remaining pill count. Returns response message."""
    s = SessionLocal()
    try:
        en = s.query(Enrollment).join(Patient).filter(
            Patient.caregiver_phone == phone,
            Enrollment.status == "active",
        ).first()
        if not en:
            return "No active enrollment found for your phone number."

        meds = s.query(EnrollmentMed).filter(
            EnrollmentMed.enrollment_id == en.id,
            EnrollmentMed.med_type == "antibiotic",
        ).all()
        if not meds:
            return "No antibiotic medications found for your enrollment."

        total_expected = sum((m.course_days or 0) * (m.doses_per_day or 1) for m in meds)
        taken = total_expected - count
        _pill_count_response[en.id] = count

        if count > total_expected * 0.5:
            # too many remaining — possible non-adherence
            return (
                f"⚠️ ಮಾತ್ರೆ ಎಣಿಕೆ: {count}/{total_expected}\n\n"
                f"ನೀವು {taken} ಮಾತ್ರೆಗಳನ್ನು ಮಾತ್ರ ತೆಗೆದುಕೊಂಡಿದ್ದೀರಿ.\n"
                f"ಔಷದ ಸಮಯಕ್ಕೆ ತೆಗೆದುಕೊಳ್ಳುವುದು ಮುಖ್ಯ.\n"
                f"ಯಾವುದೇ ತೊಂದರೆ ಇದ್ದರೆ 104 ಗೆ ಕರೆ ಮಾಡಿ."
            )
        elif count == 0:
            return (
                f"✅ ಎಲ್ಲಾ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಂಡಿದ್ದೀರಿ!\n"
                f"ಚಿಕಿತ್ಸೆ ಪೂರ್ಣಗೊಳಿಸಿದ್ದಕ್ಕಾಗಿ ಧನ್ಯವಾದಗಳು.\n"
                f"ಆರೋಗ್ಯವಾಗಿರಿ!"
            )
        else:
            return (
                f"📊 ಮಾತ್ರೆ ಎಣಿಕೆ: {count}/{total_expected}\n\n"
                f"ನೀವು {taken} ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಂಡಿದ್ದೀರಿ.\n"
                f"ಉಳಿದ {count} ಮಾತ್ರೆಗಳನ್ನು ಸಮಯಕ್ಕೆ ತೆಗೆದುಕೊಳ್ಳಿ.\n"
                f"ಯಾವುದೇ ತೊಂದರೆ ಇದ್ದರೆ 104 ಗೆ ಕರೆ ಮಾಡಿ."
            )
    finally:
        s.close()


# ── weekly summary ─────────────────────────────────────────────────────────────

def send_weekly_summary() -> bool:
    """Post weekly AMR summary to staff group. Returns True if sent."""
    chat_id = settings.TELEGRAM_CHAT_ID
    if not chat_id:
        return False

    s = SessionLocal()
    try:
        from sqlalchemy import func

        total_enrolled = s.query(Enrollment).filter(Enrollment.status == "active").count()
        total_abx = (s.query(EnrollmentMed).join(Enrollment)
                     .filter(Enrollment.status == "active",
                             EnrollmentMed.med_type == "antibiotic").count())
        open_esc = s.query(Escalation).filter(Escalation.status == "open").count()

        # completed calls this week
        week_ago = (datetime.now(timezone.utc) - timedelta(weeks=1)).isoformat()
        completed_week = s.query(FollowupCall).filter(
            FollowupCall.status == "completed",
            FollowupCall.completed_at >= week_ago,
        ).count()

        no_answer = s.query(FollowupCall).filter(
            FollowupCall.status.in_(["no_answer", "failed"]),
            FollowupCall.completed_at >= week_ago,
        ).count()

        reach_rate = round(completed_week / (completed_week + no_answer) * 100, 1) if (completed_week + no_answer) else 100.0

        # pill count responses
        pills_responded = len(_pill_count_response)
        non_adherent = sum(1 for v in _pill_count_response.values() if v > 5)

        msg = (
            f"📋 WEEKLY AMR STEWARDSHIP SUMMARY\n"
            f"{settings.HOSPITAL_NAME}\n\n"
            f"Active enrollments: {total_enrolled}\n"
            f"Antibiotic patients: {total_abx}\n"
            f"Calls completed: {completed_week}\n"
            f"No-answer/failed: {no_answer}\n"
            f"Reach rate: {reach_rate}%\n"
            f"Open escalations: {open_esc}\n"
            f"Pill count responses: {pills_responded}\n"
            f"Non-adherent: {non_adherent}\n\n"
            f"Dashboard: {settings.PUBLIC_BASE_URL}"
        )
        return _send_telegram(int(chat_id), msg)

    except Exception as e:
        log.warning("weekly summary failed: %s", e)
        return False
    finally:
        s.close()


# ── combined trigger (for demo) ────────────────────────────────────────────────

def run_full_steward_cycle() -> dict:
    """Run all steward actions. Returns summary dict."""
    reminders = send_daily_reminders()
    pill_checks = check_pill_count()
    adherence_esc = check_non_adherence()
    return {
        "reminders_sent": reminders,
        "pill_checks_sent": pill_checks,
        "non_adherence_escalations": adherence_esc,
    }
