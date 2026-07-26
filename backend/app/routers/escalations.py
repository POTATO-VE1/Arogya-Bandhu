import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import current_user
from app.models import Enrollment, Escalation, FollowupCall, Patient, User

router = APIRouter(tags=["escalations"])


def _hospital(user: User) -> str:
    return user.hospital_code


def _mask(phone: str) -> str:
    return f"{phone[:6]}•••••{phone[-3:]}" if len(phone) > 6 else "••••"


@router.get("/api/escalations")
def list_escalations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    rows = (db.query(Escalation).filter(Escalation.hospital_code == hc)
            .order_by(Escalation.status, Escalation.created_at.desc()).limit(200).all())
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
    x = db.query(Escalation).filter(Escalation.id == eid,
                                    Escalation.hospital_code == _hospital(user)).first()
    if not x:
        raise HTTPException(404)
    if x.status == "open":
        x.status = "acked"
        x.acked_by = user.id
        x.acked_at = now_utc()
        db.commit()
        write_audit(db, hospital_code=_hospital(user), actor=user.username,
                    action="ack", entity_id=x.id)
        db.commit()
    return {"status": x.status}


class ResolveBody(BaseModel):
    note: str = "resolved by staff"


@router.post("/api/escalations/{eid}/resolve")
def resolve(eid: str, body: ResolveBody,
            user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Resolve an escalation with a clinical note."""
    x = db.query(Escalation).filter(Escalation.id == eid,
                                    Escalation.hospital_code == _hospital(user)).first()
    if not x:
        raise HTTPException(404)
    if x.status not in ("open", "acked"):
        raise HTTPException(400, "escalation already resolved")
    x.status = "resolved"
    x.acked_by = x.acked_by or user.id
    x.acked_at = x.acked_at or now_utc()
    x.resolved_by = user.id
    x.resolved_at = now_utc()
    x.resolution_note = body.note
    db.commit()
    write_audit(db, hospital_code=_hospital(user), actor=user.username,
                action="resolve", entity_id=x.id, meta={"note": body.note})
    db.commit()
    return {"status": "resolved"}


@router.get("/api/dashboard/daily-stats")
def daily_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Today's summary: calls completed, risk distribution, reach rate, escalations."""
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    hc = _hospital(user)

    calls_today = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.completed_at >= today_start,
    ).count()

    green = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk == "green",
    ).count()

    yellow = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk == "yellow",
    ).count()

    red = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.completed_at >= today_start,
        FollowupCall.risk == "red",
    ).count()

    failed = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.scheduled_at >= today_start,
        FollowupCall.status.in_(["no_answer", "failed"]),
    ).count()

    total_scheduled = db.query(FollowupCall).filter(
        FollowupCall.hospital_code == hc,
        FollowupCall.scheduled_at >= today_start,
    ).count()

    open_esc = db.query(Escalation).filter(
        Escalation.hospital_code == hc,
        Escalation.status.in_(["open", "acked"]),
    ).count()

    resolved_today = db.query(Escalation).filter(
        Escalation.hospital_code == hc,
        Escalation.resolved_at >= today_start,
    ).count()

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
