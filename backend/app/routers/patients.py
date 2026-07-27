from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import desc
from sqlalchemy.orm import Session
import json

from app.db import get_db
from app.deps import current_user, can_access_enrollment
from app.fhir import build_bundle
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation, FollowupCall, Patient, User,
)
from app.security import check_ip_rate, record_ip_hit

router = APIRouter(tags=["patients"])


def _hospital(user: User) -> str:
    return user.hospital_code


@router.get("/api/board")
def board(user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    from datetime import datetime, timezone

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Role-based filtering: nurse/staff only see their ward
    base_q = db.query(Enrollment).filter(Enrollment.hospital_code == hc)
    if user.role not in ("admin", "doctor") and user.ward:
        base_q = base_q.filter(Enrollment.ward == user.ward)
    enrollments = base_q.order_by(desc(Enrollment.created_at)).limit(200).all()

    # KPIs scoped to the same filtered enrollment set
    eids = [e.id for e in enrollments]
    open_esc = (db.query(Escalation).filter(Escalation.enrollment_id.in_(eids),
                Escalation.status == "open").count()) if eids else 0
    calls_today = (db.query(FollowupCall).filter(FollowupCall.enrollment_id.in_(eids),
                   FollowupCall.scheduled_at >= start_of_day.isoformat()).count()) if eids else 0
    completed = (db.query(FollowupCall).filter(FollowupCall.enrollment_id.in_(eids),
                 FollowupCall.status == "completed").count()) if eids else 0
    noans = (db.query(FollowupCall).filter(FollowupCall.enrollment_id.in_(eids),
             FollowupCall.status.in_(["no_answer", "failed"])).count()) if eids else 0
    reach = round(completed / (completed + noans), 3) if (completed + noans) else 1.0

    # batch-load related entities (3 queries instead of N)
    eids = [e.id for e in enrollments]
    pids = [e.patient_id for e in enrollments]

    patients = {p.id: p for p in db.query(Patient).filter(Patient.id.in_(pids)).all()} if pids else {}

    all_calls = (db.query(FollowupCall)
                 .filter(FollowupCall.enrollment_id.in_(eids))
                 .order_by(FollowupCall.scheduled_at).all()) if eids else []
    calls_by_eid: dict[str, list] = defaultdict(list)
    for c in all_calls:
        calls_by_eid[c.enrollment_id].append(c)

    open_eids = {row[0] for row in (db.query(Escalation.enrollment_id)
                 .filter(Escalation.enrollment_id.in_(eids),
                         Escalation.status == "open").all())} if eids else set()

    rows = []
    for e in enrollments:
        p = patients.get(e.patient_id)
        calls = calls_by_eid.get(e.id, [])
        last = calls[-1] if calls else None
        next_pending = next((c.day_index for c in calls if c.status == "pending"), None)
        rows.append({
            "enrollment_id": e.id, "patient_id": p.id if p else None,
            "patient_name": p.name if p else "?",
            "protocol_id": e.protocol_id, "ward": e.ward,
            "day_index_next": next_pending, "last_call_status": last.status if last else None,
            "last_risk": last.risk_level if last else None,
            "number_verified": bool(e.number_verified), "open_escalation": e.id in open_eids,
            "outcome": e.outcome or None,
        })

    return {"kpis": {"enrolled": len(enrollments), "calls_today": calls_today,
                     "open_escalations": open_esc, "reach_rate": reach},
            "rows": rows}


@router.get("/api/board/whatnow")
def board_whatnow(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The "what should I do right now" panel for the dashboard.

    Three lists, each capped at 5:
    - `next_calls_due_2h`: followup_calls scheduled within the next 2 hours.
    - `stale_calls`: calls with status in (pending, ringing) AND
      `scheduled_at < now - 24h` — usually a Twilio failure, needs a manual
      re-trigger.
    - `unresolved_red`: escalations that are open or stale-acked
      (acked > 1h ago) — needs the doctor's attention.
    """
    from datetime import datetime, timezone, timedelta
    hc = _hospital(user)
    now = datetime.now(timezone.utc)
    horizon_2h = (now + timedelta(hours=2)).isoformat()
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_1h = (now - timedelta(hours=1)).isoformat()

    # Role / ward scoping
    base_q = db.query(Enrollment).filter(Enrollment.hospital_code == hc)
    if user.role not in ("admin", "doctor") and user.ward:
        base_q = base_q.filter(Enrollment.ward == user.ward)
    eids_in_scope = [e.id for e in base_q.all()]

    # next_calls_due_2h
    next_calls = []
    if eids_in_scope:
        rows = (db.query(FollowupCall, Enrollment, Patient)
                .join(Enrollment, FollowupCall.enrollment_id == Enrollment.id)
                .join(Patient, Enrollment.patient_id == Patient.id)
                .filter(FollowupCall.hospital_code == hc,
                        FollowupCall.status == "pending",
                        FollowupCall.scheduled_at <= horizon_2h,
                        FollowupCall.enrollment_id.in_(eids_in_scope))
                .order_by(FollowupCall.scheduled_at)
                .limit(5).all())
        for c, e, p in rows:
            sched = datetime.fromisoformat(c.scheduled_at)
            in_min = max(0, int((sched - now).total_seconds() / 60))
            next_calls.append({
                "enrollment_id": e.id, "patient_id": p.id, "patient_name": p.name,
                "day_index": c.day_index, "scheduled_at": c.scheduled_at,
                "in_minutes": in_min,
            })

    # stale_calls: status pending/ringing AND scheduled_at < now - 24h
    stale = []
    if eids_in_scope:
        rows = (db.query(FollowupCall, Enrollment, Patient)
                .join(Enrollment, FollowupCall.enrollment_id == Enrollment.id)
                .join(Patient, Enrollment.patient_id == Patient.id)
                .filter(FollowupCall.hospital_code == hc,
                        FollowupCall.status.in_(["pending", "ringing"]),
                        FollowupCall.scheduled_at < cutoff_24h,
                        FollowupCall.enrollment_id.in_(eids_in_scope))
                .order_by(FollowupCall.scheduled_at)
                .limit(5).all())
        for c, e, p in rows:
            sched = datetime.fromisoformat(c.scheduled_at)
            hours = int((now - sched).total_seconds() / 3600)
            stale.append({
                "enrollment_id": e.id, "patient_id": p.id, "patient_name": p.name,
                "last_call_status": c.status, "scheduled_at": c.scheduled_at,
                "hours_stale": hours,
            })

    # unresolved_red: escalations status open OR (acked AND acked_at < now-1h)
    open_esc = (db.query(Escalation)
                .filter(Escalation.hospital_code == hc,
                        Escalation.status == "open")
                .order_by(Escalation.created_at)
                .limit(5).all())
    stale_acked = (db.query(Escalation)
                   .filter(Escalation.hospital_code == hc,
                           Escalation.status == "acked",
                           Escalation.acked_at < cutoff_1h)
                   .order_by(Escalation.acked_at)
                   .limit(5).all())
    unresolved = []
    for x in open_esc + stale_acked:
        e = db.get(Enrollment, x.enrollment_id)
        p = db.get(Patient, e.patient_id) if e else None
        created = datetime.fromisoformat(x.created_at)
        age_min = int((now - created).total_seconds() / 60)
        unresolved.append({
            "escalation_id": x.id, "patient_name": p.name if p else "?",
            "enrollment_id": x.enrollment_id, "patient_id": p.id if p else None,
            "status": x.status, "age_minutes": age_min,
        })

    return {
        "next_calls_due_2h": next_calls,
        "stale_calls": stale,
        "unresolved_red": unresolved,
    }


def _escape_like(s: str) -> str:
    """Escape SQL LIKE wildcards in user input.

    Otherwise a query of `%` or `_` would act as a wildcard (matching all
    rows or single characters), which is a data-enumeration risk.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/api/patients/search")
def search_patients(request: Request, q: str = "",
                    user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Search patients by name or caregiver phone.

    Phase 1: rate-limited per IP (30 / min) and LIKE-wildcard-escaped
    to prevent data enumeration.
    """
    ip = request.client.host if request.client else "?"
    if not check_ip_rate(ip):
        raise HTTPException(429, "too many requests — slow down")
    record_ip_hit(ip)

    hc = _hospital(user)
    if not q or len(q) < 1:
        return []
    if len(q) > 100:
        q = q[:100]
    safe = _escape_like(q)
    pattern = f"%{safe}%"
    patients = (db.query(Patient)
                .filter(Patient.hospital_code == hc,
                        (Patient.name.ilike(pattern) | Patient.caregiver_phone.ilike(pattern)))
                .limit(50).all())
    return [{"id": p.id, "name": p.name, "phone": p.caregiver_phone, "age": p.age} for p in patients]


@router.get("/api/patients/export/csv")
def export_csv(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Export all patients with enrollment info as CSV."""
    import csv
    import io
    hc = _hospital(user)
    enrollments = (db.query(Enrollment).filter(Enrollment.hospital_code == hc)
                   .order_by(desc(Enrollment.created_at)).limit(500).all())
    pids = list({e.patient_id for e in enrollments})
    patients = {p.id: p for p in db.query(Patient).filter(Patient.id.in_(pids)).all()} if pids else {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Patient ID", "Name", "Age", "Sex", "Phone", "Caregiver",
                "Protocol", "Condition", "Ward", "Discharge Date", "Status", "Outcome", "Verified"])
    for e in enrollments:
        p = patients.get(e.patient_id)
        if not p:
            continue
        w.writerow([p.id, p.name, p.age or "", p.sex or "", p.caregiver_phone,
                    p.caregiver_name, e.protocol_id, e.condition_label,
                    e.ward or "", e.discharge_date, e.status, e.outcome or "",
                    "Yes" if e.number_verified else "No"])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=patients_export.csv"})


@router.get("/api/patients/{pid}")
def patient_detail(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    p = db.query(Patient).filter(Patient.id == pid, Patient.hospital_code == hc).first()
    if not p:
        raise HTTPException(404)  # IDOR-safe (docs/02 §7.4): never 403
    # Role-based access: use helper for ward check
    if user.role not in ("admin", "doctor") and user.ward:
        e = db.query(Enrollment).filter(Enrollment.patient_id == p.id).first()
        if e and not can_access_enrollment(user, e.ward):
            raise HTTPException(404)  # pretend not found
    enrollments = []
    for e in db.query(Enrollment).filter(Enrollment.patient_id == p.id).all():
        meds = [dict(name=m.med_name, type=m.med_type, doses=m.doses_per_day)
                for m in db.query(EnrollmentMed).filter(EnrollmentMed.enrollment_id == e.id).all()]
        calls = []
        for c in (db.query(FollowupCall).filter(FollowupCall.enrollment_id == e.id)
                  .order_by(FollowupCall.scheduled_at).all()):
            responses = [dict(node_id=r.node_id, digit=r.digit, score=r.score)
                         for r in (db.query(CallResponse).filter(CallResponse.call_id == c.id))]
            calls.append(dict(id=c.id, day_index=c.day_index, status=c.status,
                              risk=c.risk_level, risk_reasons=c.risk_reasons,
                              scheduled_at=c.scheduled_at, provider=c.provider,
                              responses=responses))
        escalations = [dict(id=x.id, level=x.level, status=x.status,
                            reasons=x.reasons, created_at=x.created_at,
                            acked_by=x.acked_by, acked_at=x.acked_at)
                       for x in db.query(Escalation).filter(Escalation.enrollment_id == e.id)]
        enrollments.append(dict(id=e.id, protocol_id=e.protocol_id,
                                condition_label=e.condition_label, ward=e.ward,
                                status=e.status, number_verified=bool(e.number_verified),
                                meds=meds, calls=calls, escalations=escalations))
    return dict(id=p.id, name=p.name, age=p.age, sex=p.sex,
                caregiver_name=p.caregiver_name, caregiver_phone=p.caregiver_phone,
                abha_number=p.abha_number, created_at=p.created_at, enrollments=enrollments)


@router.get("/api/patients/{pid}/fhir")
def patient_fhir(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    p = db.query(Patient).filter(Patient.id == pid, Patient.hospital_code == hc).first()
    if not p:
        raise HTTPException(404)
    e = (db.query(Enrollment).filter(Enrollment.patient_id == p.id)
         .order_by(desc(Enrollment.created_at)).first())
    if not e:
        raise HTTPException(404, "no enrollment to export")
    meds = (db.query(EnrollmentMed).filter(EnrollmentMed.enrollment_id == e.id).all())
    bundle = build_bundle(p, e, meds)
    return Response(content=json.dumps(bundle, indent=2, ensure_ascii=False),
                    media_type="application/fhir+json")


from pydantic import BaseModel as _BaseModel

class OutcomeBody(_BaseModel):
    outcome: str


@router.post("/api/enrollments/{eid}/outcome")
def set_outcome(eid: str, body: OutcomeBody, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Set patient outcome (recovered/readmitted/referred)."""
    e = db.query(Enrollment).filter(Enrollment.id == eid,
                                     Enrollment.hospital_code == _hospital(user)).first()
    if not e:
        raise HTTPException(404)
    if body.outcome not in (
        "recovered", "readmitted", "referred",
        "deceased", "lost_to_followup", "transferred",
    ):
        raise HTTPException(400, "Invalid outcome")
    e.outcome = body.outcome
    db.commit()
    # Audit the outcome change
    from app.audit import write_audit
    write_audit(db, hospital_code=_hospital(user), actor=user.username,
                action="set_outcome", entity_id=e.id,
                meta={"outcome": body.outcome})
    db.commit()
    return {"status": "ok", "outcome": e.outcome}