import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import apply_hospital_scope, current_user, hospital_scope
from app.models import Enrollment, Escalation, FollowupCall, Patient, User

router = APIRouter(tags=["escalations"])


def _hospital(user: User) -> str | None:
    """Return the hospital_code to filter by, or None for superadmin (no filter)."""
    return None if user.role == "superadmin" else user.hospital_code


def _mask(phone: str) -> str:
    return f"{phone[:6]}•••••{phone[-3:]}" if len(phone) > 6 else "••••"


@router.get("/api/escalations")
def list_escalations(
    hospital: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Escalation)
    if user.role == "superadmin" and hospital:
        q = q.filter(Escalation.hospital_code == hospital)
    elif user.role != "superadmin":
        q = q.filter(Escalation.hospital_code == user.hospital_code)
    rows = (q.order_by(Escalation.status, Escalation.created_at.desc())
              .limit(200).all())
    out = []
    for x in rows:
        en = db.query(Enrollment).filter(Enrollment.id == x.enrollment_id).first()
        p = db.query(Patient).filter(Patient.id == en.patient_id).first() if en else None

        # fetch the triggering call's transcript if available
        call_transcript = []
        if x.call_id:
            call = db.query(FollowupCall).filter(FollowupCall.id == x.call_id).first()
            if call and call.responses:
                call_transcript = [
                    {"node_id": r.node_id, "digit": r.digit, "score": r.score}
                    for r in call.responses
                ]

        out.append({
            "id": x.id,
            "patient_name": p.name if p else "?",
            "caregiver_phone": _mask(p.caregiver_phone) if p else "",
            "protocol_id": en.protocol_id if en else "",
            "level": x.level,
            "reasons": json.loads(x.reasons) if x.reasons and x.reasons.strip() else [],
            "status": x.status,
            "created_at": x.created_at,
            "acked_by": x.acked_by,
            "acked_at": x.acked_at,
            "resolved_by": x.resolved_by,
            "resolved_at": x.resolved_at,
            "resolution_note": x.resolution_note,
            "enrollment_id": x.enrollment_id,
            "patient_id": p.id if p else None,
            "call_transcript": call_transcript,
        })
    return out


@router.post("/api/escalations/{eid}/ack")
def ack(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Look up by uuid only; verify hospital_code after the fetch.
    x = db.query(Escalation).filter(Escalation.id == eid).first()
    if not x:
        raise HTTPException(404)
    if user.role != "superadmin" and x.hospital_code != user.hospital_code:
        raise HTTPException(404)  # IDOR-safe: pretend not found
    if x.status == "open":
        x.status = "acked"
        x.acked_by = user.id
        x.acked_at = now_utc()
        db.commit()
        write_audit(db, hospital_code=x.hospital_code, actor=user.username,
                    action="ack", entity_id=x.id)
        db.commit()
    return {"status": x.status}


class ResolveBody(BaseModel):
    note: str = "resolved by staff"
    disposition: str | None = None
    callback_in_hours: int | None = None


@router.post("/api/escalations/{eid}/resolve")
def resolve(eid: str, body: ResolveBody,
            user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Resolve an escalation. Optional `disposition` (categorical) and
    `callback_in_hours` (1..72) schedule a new manual-callback followup_call
    that will fire at `now + hours`."""
    x = db.query(Escalation).filter(Escalation.id == eid).first()
    if not x:
        raise HTTPException(404)
    if user.role != "superadmin" and x.hospital_code != user.hospital_code:
        raise HTTPException(404)
    if x.status not in ("open", "acked"):
        raise HTTPException(400, "escalation already resolved")

    # Validate disposition
    allowed_dispositions = {
        "called_family", "advised_er_visit", "meds_adjusted",
        "stable_no_action", "referred", "callback_scheduled",
    }
    disposition = body.disposition
    if disposition is not None and disposition not in allowed_dispositions:
        raise HTTPException(400, f"Invalid disposition. Allowed: {sorted(allowed_dispositions)}")
    # Validate callback_in_hours range
    if body.callback_in_hours is not None and not (1 <= body.callback_in_hours <= 72):
        raise HTTPException(400, "callback_in_hours must be between 1 and 72")

    x.status = "resolved"
    x.acked_by = x.acked_by or user.id
    x.acked_at = x.acked_at or now_utc()
    x.resolved_by = user.id
    x.resolved_at = now_utc()
    x.resolution_note = body.note
    db.commit()

    # Schedule a manual callback if requested
    new_call_id: str | None = None
    if body.callback_in_hours is not None:
        from datetime import datetime, timedelta, timezone
        from app.models import FollowupCall
        # If disposition not specified, default to callback_scheduled
        if disposition is None:
            disposition = "callback_scheduled"
        scheduled_at = (datetime.now(timezone.utc)
                        + timedelta(hours=body.callback_in_hours)).isoformat()
        c = FollowupCall(
            hospital_code=x.hospital_code,
            enrollment_id=x.enrollment_id,
            day_index=0,
            scheduled_at=scheduled_at,
            kind="manual_callback",
            status="pending",
            triggered_by=user.id,
        )
        db.add(c); db.commit(); db.refresh(c)
        new_call_id = c.id
        # Schedule the callback with the APScheduler. We wrap in try/except
        # because in test environments the scheduler may not be started;
        # the row is still scheduled and will be picked up by
        # reschedule_pending() on next startup.
        try:
            from app.scheduler import schedule_call
            schedule_call(c.id, scheduled_at)
        except Exception:
            pass

    write_audit(db, hospital_code=x.hospital_code, actor=user.username,
                action="resolve", entity_id=x.id, meta={
                    "note": body.note,
                    "disposition": disposition,
                    "callback_in_hours": body.callback_in_hours,
                })
    db.commit()
    return {
        "status": "resolved",
        "disposition": disposition,
        "callback_call_id": new_call_id,
    }


@router.get("/api/dashboard/daily-stats")
def daily_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Today's summary: calls completed, risk distribution, reach rate, escalations.

    Backwards-compat endpoint — prefer `GET /api/dashboard/overview` for the
    full set of KPIs. Superadmin sees across all hospitals.
    """
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    is_super = user.role == "superadmin"
    hc = user.hospital_code

    def _f(q):
        if is_super:
            return q
        return q.filter(FollowupCall.hospital_code == hc)

    calls_today = _f(db.query(FollowupCall).filter(
        FollowupCall.completed_at >= today_start)).count()
    green = _f(db.query(FollowupCall).filter(
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk_level == "green")).count()
    yellow = _f(db.query(FollowupCall).filter(
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk_level == "yellow")).count()
    red = _f(db.query(FollowupCall).filter(
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk_level == "red")).count()
    failed = _f(db.query(FollowupCall).filter(
        FollowupCall.scheduled_at >= today_start,
        FollowupCall.status.in_(["no_answer", "failed"]))).count()
    total_scheduled = _f(db.query(FollowupCall).filter(
        FollowupCall.scheduled_at >= today_start)).count()

    def _e(q):
        if is_super:
            return q
        return q.filter(Escalation.hospital_code == hc)

    open_esc = _e(db.query(Escalation).filter(
        Escalation.status.in_(["open", "acked"]))).count()
    resolved_today = _e(db.query(Escalation).filter(
        Escalation.resolved_at >= today_start)).count()

    return {
        "calls_today": calls_today,
        "risk_green": green,
        "risk_yellow": yellow,
        "risk_red": red,
        "calls_failed": failed,
        "calls_scheduled": total_scheduled,
        "open_escalations": open_esc,
        "resolved_today": resolved_today,
        "reach_rate": round(calls_today / total_scheduled * 100, 1) if total_scheduled else 0,
    }


@router.get("/api/dashboard/risk-trend")
def risk_trend(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Weekly risk distribution for last 8 weeks. Superadmin sees across all hospitals."""
    from datetime import datetime, timedelta, timezone
    is_super = user.role == "superadmin"
    hc = user.hospital_code
    now = datetime.now(timezone.utc)
    weeks = []
    for i in range(7, -1, -1):
        week_end = now - timedelta(weeks=i)
        week_start = week_end - timedelta(days=7)
        start_str = week_start.isoformat()
        end_str = week_end.isoformat()

        def _filt(q):
            if is_super:
                return q
            return q.filter(FollowupCall.hospital_code == hc)

        green = _filt(db.query(FollowupCall).filter(
            FollowupCall.completed_at >= start_str,
            FollowupCall.completed_at < end_str,
            FollowupCall.risk_level == "green")).count()
        yellow = _filt(db.query(FollowupCall).filter(
            FollowupCall.completed_at >= start_str,
            FollowupCall.completed_at < end_str,
            FollowupCall.risk_level == "yellow")).count()
        red = _filt(db.query(FollowupCall).filter(
            FollowupCall.completed_at >= start_str,
            FollowupCall.completed_at < end_str,
            FollowupCall.risk_level == "red")).count()
        weeks.append({
            "label": week_start.strftime("%b %d"),
            "green": green, "yellow": yellow, "red": red,
        })
    return weeks


@router.get("/api/dashboard/nurse-metrics")
def nurse_metrics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Per-nurse performance: calls made, escalations resolved, avg response time."""
    from datetime import datetime, timezone
    is_super = user.role == "superadmin"
    hc = user.hospital_code
    # staff scope: all hospitals for superadmin, just one for others
    n_q = db.query(User).filter(User.role.in_(["nurse", "admin", "doctor"]))
    if not is_super:
        n_q = n_q.filter(User.hospital_code == hc)
    nurses = n_q.all()
    result = []
    for n in nurses:
        if is_super:
            calls_made = (db.query(FollowupCall)
                          .filter(FollowupCall.triggered_by == n.id).count())
            esc_resolved = (db.query(Escalation)
                            .filter(Escalation.resolved_by == n.id).count())
            esc_total = db.query(Escalation).filter(
                Escalation.status == "resolved").count()
        else:
            calls_made = db.query(FollowupCall).filter(
                FollowupCall.hospital_code == hc,
                FollowupCall.triggered_by == n.id,
            ).count()
            esc_resolved = db.query(Escalation).filter(
                Escalation.hospital_code == hc,
                Escalation.resolved_by == n.id,
            ).count()
            esc_total = db.query(Escalation).filter(
                Escalation.hospital_code == hc,
                Escalation.status == "resolved",
            ).count()
        result.append({
            "username": n.username,
            "display_name": n.display_name,
            "calls_made": calls_made,
            "escalations_resolved": esc_resolved,
            "resolution_rate": round(esc_resolved / esc_total * 100, 1) if esc_total else 0,
        })
    return result
