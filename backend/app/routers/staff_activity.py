"""Staff-specific endpoints — activity feed, my patients, patient timeline."""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user, can_access_enrollment
from app.models import (
    AuditLog, Enrollment, Escalation, FollowupCall, Patient, User,
)

router = APIRouter(tags=["staff"])


def _hospital(user: User) -> str:
    return user.hospital_code


# ── GET /api/staff/activity ──────────────────────────────────────────────────
# Returns the audit trail for the current user (what they did).

@router.get("/api/staff/activity")
def my_activity(
    limit: int = 50,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Activity feed for the current user — what they did."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.hospital_code == _hospital(user), AuditLog.actor == user.username)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
        .all()
    )

    # resolve entity_ids to human-readable names
    entity_map = _resolve_entities(db, [l.entity_id for l in logs if l.entity_id])

    return [
        {
            "id": l.id,
            "action": l.action,
            "entity_id": l.entity_id,
            "entity_name": entity_map.get(l.entity_id, l.entity_id),
            "meta": json.loads(l.meta) if l.meta else None,
            "created_at": l.created_at,
        }
        for l in logs
    ]


# ── GET /api/staff/patients ──────────────────────────────────────────────────
# Returns patients the current user has interacted with.

@router.get("/api/staff/patients")
def my_patients(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Patients handled by or assigned to the current user."""
    hc = _hospital(user)

    if user.role in ("admin", "doctor"):
        # see all patients in hospital
        enrollments = (
            db.query(Enrollment)
            .filter(Enrollment.hospital_code == hc)
            .order_by(desc(Enrollment.created_at))
            .limit(200)
            .all()
        )
    elif user.ward:
        # nurse/staff: only patients in their ward
        enrollments = (
            db.query(Enrollment)
            .filter(Enrollment.hospital_code == hc, Enrollment.ward == user.ward)
            .order_by(desc(Enrollment.created_at))
            .limit(200)
            .all()
        )
    else:
        # no ward assigned: show patients they created/enrolled
        enrollments = (
            db.query(Enrollment)
            .filter(Enrollment.hospital_code == hc, Enrollment.created_by == user.id)
            .order_by(desc(Enrollment.created_at))
            .limit(200)
            .all()
        )

    # batch load
    eids = [e.id for e in enrollments]
    pids = [e.patient_id for e in enrollments]
    patients = {p.id: p for p in db.query(Patient).filter(Patient.id.in_(pids)).all()} if pids else {}

    # count calls per enrollment
    all_calls = (
        db.query(FollowupCall)
        .filter(FollowupCall.enrollment_id.in_(eids))
        .order_by(FollowupCall.scheduled_at)
        .all()
    ) if eids else []
    calls_by_eid: dict[str, list] = defaultdict(list)
    for c in all_calls:
        calls_by_eid[c.enrollment_id].append(c)

    # open escalations
    open_eids = {
        row[0]
        for row in (
            db.query(Escalation.enrollment_id)
            .filter(Escalation.enrollment_id.in_(eids), Escalation.status == "open")
            .all()
        )
    } if eids else set()

    rows = []
    for e in enrollments:
        p = patients.get(e.patient_id)
        calls = calls_by_eid.get(e.id, [])
        last = calls[-1] if calls else None
        next_pending = next((c.day_index for c in calls if c.status == "pending"), None)
        rows.append({
            "enrollment_id": e.id,
            "patient_id": p.id if p else None,
            "patient_name": p.name if p else "?",
            "phone": p.caregiver_phone if p else None,
            "protocol_id": e.protocol_id,
            "ward": e.ward,
            "condition": e.condition_label,
            "discharge_date": e.discharge_date,
            "day_index_next": next_pending,
            "last_call_status": last.status if last else None,
            "last_risk": last.risk_level if last else None,
            "open_escalation": e.id in open_eids,
            "outcome": e.outcome,
            "created_by": e.created_by,
            "created_at": e.created_at,
        })

    return rows


# ── GET /api/patients/{pid}/timeline ─────────────────────────────────────────
# Full timeline of who did what for a specific patient.

@router.get("/api/patients/{pid}/timeline")
def patient_timeline(
    pid: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Full activity timeline for a patient — who did what, when."""
    hc = _hospital(user)
    p = db.query(Patient).filter(Patient.id == pid, Patient.hospital_code == hc).first()
    if not p:
        raise HTTPException(404)

    # role check — use can_access_enrollment helper for consistency
    if user.role not in ("admin", "doctor") and user.ward:
        e_check = db.query(Enrollment).filter(Enrollment.patient_id == p.id).first()
        if e_check and not can_access_enrollment(user, e_check.ward):
            raise HTTPException(404)

    enrollments = (
        db.query(Enrollment)
        .filter(Enrollment.patient_id == p.id)
        .order_by(desc(Enrollment.created_at))
        .all()
    )
    eids = [e.id for e in enrollments]

    # batch load
    all_calls = (
        db.query(FollowupCall)
        .filter(FollowupCall.enrollment_id.in_(eids))
        .order_by(FollowupCall.scheduled_at)
        .all()
    ) if eids else []
    all_escalations = (
        db.query(Escalation)
        .filter(Escalation.enrollment_id.in_(eids))
        .order_by(Escalation.created_at)
        .all()
    ) if eids else []

    # get audit logs for this patient's enrollments
    audit_logs = (
        db.query(AuditLog)
        .filter(AuditLog.entity_id.in_(eids + [p.id]))
        .order_by(AuditLog.created_at)
        .all()
    ) if eids else []

    # resolve usernames to display names
    actor_names = _resolve_usernames(db, list({l.actor for l in audit_logs}))

    # build timeline
    events = []

    # patient creation
    creator = _resolve_user_id(db, p.created_by)
    events.append({
        "type": "patient_created",
        "actor": creator,
        "actor_name": creator,
        "timestamp": p.created_at,
        "detail": f"Patient {p.name} registered",
    })

    # enrollments
    for e in enrollments:
        enroller = _resolve_user_id(db, e.created_by)
        events.append({
            "type": "enrollment",
            "actor": enroller,
            "actor_name": enroller,
            "timestamp": e.created_at,
            "detail": f"Enrolled in {e.protocol_id} ({e.condition_label})",
        })

    # calls
    for c in all_calls:
        triggerer = _resolve_user_id(db, c.triggered_by) if c.triggered_by else "system"
        events.append({
            "type": "call",
            "actor": triggerer,
            "actor_name": triggerer,
            "timestamp": c.scheduled_at,
            "detail": f"Day {c.day_index} call — {c.status}",
            "risk": c.risk_level,
        })

    # escalations
    for esc in all_escalations:
        acker = _resolve_user_id(db, esc.acked_by) if esc.acked_by else None
        resolver = _resolve_user_id(db, esc.resolved_by) if esc.resolved_by else None
        events.append({
            "type": "escalation",
            "actor": "system",
            "actor_name": "system",
            "timestamp": esc.created_at,
            "detail": f"Escalation [{esc.level.upper()}] — {esc.status}",
            "acked_by": acker,
            "resolved_by": resolver,
        })

    # outcome changes from audit
    for l in audit_logs:
        if l.action == "set_outcome":
            meta = json.loads(l.meta) if l.meta else {}
            events.append({
                "type": "outcome",
                "actor": actor_names.get(l.actor, l.actor),
                "actor_name": actor_names.get(l.actor, l.actor),
                "timestamp": l.created_at,
                "detail": f"Outcome set to {meta.get('outcome', '?')}",
            })

    # sort by timestamp
    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return {
        "patient": {
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "sex": p.sex,
            "phone": p.caregiver_phone,
        },
        "timeline": events,
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _resolve_entities(db: Session, entity_ids: list[str | None]) -> dict[str, str]:
    """Resolve entity_ids to human-readable names."""
    ids = [eid for eid in entity_ids if eid]
    if not ids:
        return {}

    result = {}

    patients = db.query(Patient).filter(Patient.id.in_(ids)).all()
    for p in patients:
        result[p.id] = f"Patient: {p.name}"

    enrollments = db.query(Enrollment).filter(Enrollment.id.in_(ids)).all()
    for e in enrollments:
        result[e.id] = f"Enrollment: {e.protocol_id}"

    return result


def _resolve_user_id(db: Session, user_id: str | None) -> str:
    """Resolve a user_id to display_name."""
    if not user_id:
        return "system"
    u = db.query(User).filter(User.id == user_id).first()
    return u.display_name if u else user_id


def _resolve_usernames(db: Session, usernames: list[str]) -> dict[str, str]:
    """Resolve usernames to display_names."""
    if not usernames:
        return {}
    users = db.query(User).filter(User.username.in_(usernames)).all()
    return {u.username: u.display_name for u in users}
