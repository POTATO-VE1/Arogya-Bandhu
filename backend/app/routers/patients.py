from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import desc
from sqlalchemy.orm import Session
import json

from app.db import get_db
from app.deps import current_user
from app.fhir import build_bundle
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation, FollowupCall, Patient, User,
)

router = APIRouter(tags=["patients"])


def _hospital(user: User) -> str:
    return user.hospital_code


@router.get("/api/board")
def board(user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    from datetime import datetime, timezone

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    enrollments = (db.query(Enrollment).filter(Enrollment.hospital_code == hc)
                   .order_by(desc(Enrollment.created_at)).limit(200).all())
    open_esc = (db.query(Escalation).filter(Escalation.hospital_code == hc,
                Escalation.status == "open").count())
    calls_today = (db.query(FollowupCall).filter(FollowupCall.hospital_code == hc,
                   FollowupCall.scheduled_at >= start_of_day.isoformat()).count())
    completed = (db.query(FollowupCall).filter(FollowupCall.hospital_code == hc,
                 FollowupCall.status == "completed").count())
    noans = (db.query(FollowupCall).filter(FollowupCall.hospital_code == hc,
             FollowupCall.status.in_(["no_answer", "failed"])).count())
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
        })

    return {"kpis": {"enrolled": len(enrollments), "calls_today": calls_today,
                     "open_escalations": open_esc, "reach_rate": reach},
            "rows": rows}


@router.get("/api/patients/{pid}")
def patient_detail(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    hc = _hospital(user)
    p = db.query(Patient).filter(Patient.id == pid, Patient.hospital_code == hc).first()
    if not p:
        raise HTTPException(404)  # IDOR-safe (docs/02 §7.4): never 403
    enrollments = []
    for e in db.query(Enrollment).filter(Enrollment.patient_id == p.id).all():
        meds = [dict(name=m.med_name, type=m.med_type, aware=m.aware_category,
                     course_days=m.course_days, doses=m.doses_per_day)
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