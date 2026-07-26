"""Call scheduler (docs/02 §8). APScheduler BackgroundScheduler + SQLAlchemyJobStore
on the same SQLite file. Jobs place real Twilio calls via the adapter; restart-safe
because pending rows are re-registered on startup.
"""
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from app.config import settings
from app.db import SessionLocal
from app.ivr.twilio_adapter import is_configured, place_call
from app.models import FollowupCall
from app.tzutil import clamp_to_calling_window

_scheduler: BackgroundScheduler | None = None


def _jobstore_url() -> str:
    # apscheduler needs a sqlalchemy URL; reuse the app DB
    return settings.DATABASE_URL


def _place_due_call(call_id: str) -> None:
    """Job body: load call, enforce window + allowlist, place call, set ringing."""
    from sqlalchemy.orm import joinedload
    s = SessionLocal()
    try:
        call = (s.query(FollowupCall)
                .options(joinedload(FollowupCall.enrollment)
                         .joinedload(Enrollment.patient))
                .filter(FollowupCall.id == call_id).first())
        if not call or call.status != "pending":
            return
        run_at = clamp_to_calling_window(call.scheduled_at)
        call.scheduled_at = run_at
        s.commit()
        if not is_configured():
            return  # dev: leave pending; demo console (T16) or twilio gate will fire it
        from app.routers.webhooks import _urls
        voice, gather, status = _urls(call_id)
        try:
            sid = place_call(
                call_id=call_id,
                to_number=call.enrollment.patient.caregiver_phone,
                voice_url=voice,
                status_callback=status,
            )
            call.provider = "twilio"
            call.provider_call_sid = sid
            call.status = "ringing"
            s.commit()
        except (PermissionError, RuntimeError):
            # not allowlisted / not configured → leave pending, will be retried by gate
            return
    finally:
        s.close()


def ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=_jobstore_url())},
        timezone="UTC",
    )
    sched.start()
    _scheduler = sched
    return sched


def schedule_call(call_id: str, utc_iso: str) -> None:
    sched = ensure_scheduler()
    run_at = clamp_to_calling_window(utc_iso)
    from datetime import datetime, timezone

    dt = datetime.fromisoformat(run_at)
    sched.add_job(
        _place_due_call, trigger=DateTrigger(run_date=dt), args=[call_id],
        id=f"call:{call_id}", replace_existing=True,
    )


def reschedule_pending() -> int:
    """On startup, re-register jobs for pending rows. Returns count registered."""
    from datetime import datetime, timezone

    sched = ensure_scheduler()
    s = SessionLocal()
    n = 0
    try:
        rows = s.query(FollowupCall).filter(FollowupCall.status == "pending").all()
        now = datetime.now(timezone.utc)
        for c in rows:
            dt = datetime.fromisoformat(c.scheduled_at)
            trig = DateTrigger(run_date=dt)
            sched.add_job(_place_due_call, trigger=trig, args=[c.id],
                          id=f"call:{c.id}", replace_existing=True)
            n += 1
    finally:
        s.close()
    return n


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None