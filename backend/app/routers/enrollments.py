import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.db import get_db, now_utc
from app.deps import current_user
from app.ivr.twilio_adapter import is_configured, place_call
from app.llm import default_sheet, personalize_sheet
from app.llm import enabled as llm_enabled, suggest_protocol
from app.models import Enrollment, EnrollmentMed, FollowupCall, Patient, User
from app.protocol_loader import get_protocol
from app.scheduler import schedule_call
from app.tzutil import schedule_at_ist_10
from app.routers.webhooks import _urls

router = APIRouter(tags=["enrollments"])


class SuggestIn(BaseModel):
    condition_label: str = ""
    free_text: str | None = None


@router.post("/api/enrollments/suggest")
def suggest(body: SuggestIn, _user: User = Depends(current_user)):
    if not llm_enabled():
        raise HTTPException(503, "llm disabled")
    res = suggest_protocol(body.free_text or body.condition_label)
    if not res:
        raise HTTPException(502, "llm returned no usable suggestion")
    return res


class MedIn(BaseModel):
    med_name: str = Field(max_length=200)
    med_type: str = Field(default="other", max_length=50)
    aware_category: str | None = Field(default=None, max_length=20)
    course_days: int | None = Field(default=None, ge=1, le=365)
    doses_per_day: int = Field(default=3, ge=1, le=20)


class PatientIn(BaseModel):
    name: str = Field(max_length=200)
    age: int | None = Field(default=None, ge=0, le=150)
    sex: str | None = Field(default=None, max_length=10)
    abha_number: str | None = Field(default=None, max_length=20)
    caregiver_name: str = Field(max_length=200)
    caregiver_phone: str = Field(max_length=20)


class EnrollIn(BaseModel):
    patient: PatientIn
    protocol_id: str = Field(max_length=50)
    condition_label: str = Field(max_length=200)
    ward: str | None = Field(default=None, max_length=100)
    discharge_date: str = Field(max_length=10)  # YYYY-MM-DD
    meds: list[MedIn] = Field(default_factory=list)
    consent: bool = True


def _hospital(user: User) -> str:
    return user.hospital_code


@router.get("/api/enrollments/{eid}/sheet")
def sheet(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = db.query(Enrollment).filter(Enrollment.id == eid,
                                    Enrollment.hospital_code == _hospital(user)).first()
    if not e:
        raise HTTPException(404)
    p = db.query(Patient).filter(Patient.id == e.patient_id).first()
    meds = (db.query(EnrollmentMed).filter(EnrollmentMed.enrollment_id == e.id).all())
    from app.protocol_loader import get_protocol
    proto = get_protocol(e.protocol_id)
    sheet_instr = json.loads(e.sheet_instructions) if e.sheet_instructions else {
        "bullets_kn": proto.get("sheet", {}).get("bullets_kn", [])[:5], "source": "template"}
    return {
        "hospital_name": settings.HOSPITAL_NAME,
        "patient_name": p.name if p else "?",
        "age": p.age if p else None,
        "sex": p.sex if p else None,
        "condition_label": e.condition_label,
        "discharge_date": e.discharge_date,
        "bullets_kn": sheet_instr.get("bullets_kn", []),
        "sheet_source": sheet_instr.get("source", "template"),
        "schedule_days": proto["schedule_days"],
        "meds": [{"name": m.med_name, "aware": m.aware_category,
                  "course_days": m.course_days, "doses_per_day": m.doses_per_day}
                 for m in meds],
        "telephones": "104 / 108",
    }


@router.post("/api/enrollments", status_code=201)
def enroll(body: EnrollIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.consent:
        raise HTTPException(422, "consent is required")

    try:
        proto = get_protocol(body.protocol_id)
    except Exception:
        raise HTTPException(422, "unknown protocol_id")

    phone = body.patient.caregiver_phone.strip()
    if not (phone.startswith("+") and phone[1:].isdigit() and len(phone) >= 10):
        raise HTTPException(422, "caregiver_phone must be E.164")

    hc = _hospital(user)

    # dedup: reject if same patient+protocol already has an active enrollment
    existing = (db.query(Enrollment).join(Patient)
                .filter(Patient.caregiver_phone == phone,
                        Patient.hospital_code == hc,
                        Enrollment.protocol_id == proto["id"],
                        Enrollment.status == "active").first())
    if existing:
        raise HTTPException(409, "patient already enrolled in this protocol")

    p = Patient(hospital_code=hc, **body.patient.model_dump(),
                consent_at=now_utc(), created_by=user.id)
    db.add(p); db.commit(); db.refresh(p)

    e = Enrollment(hospital_code=hc, patient_id=p.id, protocol_id=proto["id"],
                  condition_label=body.condition_label, ward=body.ward,
                  discharge_date=body.discharge_date)
    db.add(e); db.commit(); db.refresh(e)

    for m in body.meds:
        db.add(EnrollmentMed(enrollment_id=e.id, **m.model_dump()))
    db.commit()

    # sheet_instructions: template default now, upgrade via LLM later (docs/03 §10.2)
    bullets = proto.get("sheet", {}).get("bullets_kn", [])
    sheet = personalize_sheet(bullets, body.condition_label) if llm_enabled() else None
    if sheet is None:
        sheet = default_sheet(bullets)
    e.sheet_instructions = json.dumps(sheet, ensure_ascii=False)
    db.commit()

    call_ids = []
    for day in proto["schedule_days"]:
        c = FollowupCall(
            hospital_code=hc, enrollment_id=e.id, day_index=day,
            scheduled_at=schedule_at_ist_10(body.discharge_date, day),
            kind="followup",
        )
        db.add(c); db.commit(); db.refresh(c)
        call_ids.append(c.id)
        schedule_call(c.id, c.scheduled_at)

    write_audit(db, hospital_code=hc, actor=user.username, action="enroll",
                entity_id=e.id, meta={"calls": len(call_ids), "consent": True})
    db.commit()
    return {"enrollment_id": e.id, "patient_id": p.id, "call_ids": call_ids}


@router.post("/api/enrollments/{eid}/verify-number")
def verify_number(eid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = db.query(Enrollment).filter(Enrollment.id == eid,
                                    Enrollment.hospital_code == _hospital(user)).first()
    if not e:
        raise HTTPException(404)
    p = db.query(Patient).filter(Patient.id == e.patient_id).first()
    if not is_configured():
        raise HTTPException(503, "twilio not configured")
    pending = db.query(FollowupCall).filter(
        FollowupCall.enrollment_id == eid,
        FollowupCall.kind == "verify",
        FollowupCall.status.in_(["pending", "in_progress", "ringing"]),
    ).first()
    if pending:
        raise HTTPException(409, "verification already in progress")
    c = FollowupCall(hospital_code=_hospital(user), enrollment_id=e.id,
                     day_index=0, scheduled_at=now_utc(), kind="verify",
                     status="ringing")
    db.add(c); db.commit(); db.refresh(c)
    voice, _, status_cb = _urls(c.id)
    try:
        sid = place_call(call_id=c.id, to_number=p.caregiver_phone,
                         voice_url=voice, status_callback=status_cb)
    except (PermissionError, RuntimeError) as ex:
        raise HTTPException(503, str(ex))
    c.provider = "twilio"; c.provider_call_sid = sid
    db.commit()
    write_audit(db, hospital_code=_hospital(user), actor=user.username,
                action="verify_number", entity_id=e.id)
    db.commit()
    return {"call_id": c.id}


class TriggerIn(BaseModel):
    enrollment_id: str
    channel: str = "twilio"  # 'twilio' | 'sim'


@router.post("/api/demo/trigger-call")
def trigger_call(body: TriggerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = (db.query(Enrollment).filter(Enrollment.id == body.enrollment_id,
                                     Enrollment.hospital_code == _hospital(user)).first())
    if not e:
        raise HTTPException(404)
    p = db.query(Patient).filter(Patient.id == e.patient_id).first()

    # dedup: reject if a demo call is already active for this enrollment
    active_demo = db.query(FollowupCall).filter(
        FollowupCall.enrollment_id == e.id,
        FollowupCall.kind == "demo",
        FollowupCall.status.in_(["pending", "in_progress", "ringing"]),
    ).first()
    if active_demo:
        raise HTTPException(409, "demo call already active")

    if body.channel == "sim":
        c = FollowupCall(hospital_code=_hospital(user), enrollment_id=e.id,
                         day_index=0, scheduled_at=now_utc(), provider="sim",
                         kind="demo", status="pending")
        db.add(c); db.commit(); db.refresh(c)
        write_audit(db, hospital_code=_hospital(user), actor=user.username,
                    action="trigger_call", entity_id=c.id, meta={"channel": "sim"})
        db.commit()
        return {"call_id": c.id}

    if not is_configured():
        raise HTTPException(503, "twilio not configured")
    c = FollowupCall(hospital_code=_hospital(user), enrollment_id=e.id,
                     day_index=0, scheduled_at=now_utc(), provider="twilio",
                     kind="demo", status="ringing")
    db.add(c); db.commit(); db.refresh(c)
    voice, _, status_cb = _urls(c.id)
    try:
        sid = place_call(call_id=c.id, to_number=p.caregiver_phone,
                         voice_url=voice, status_callback=status_cb)
    except (PermissionError, RuntimeError) as ex:
        c.status = "failed"; db.commit()
        raise HTTPException(503, str(ex))
    c.provider_call_sid = sid; db.commit()
    write_audit(db, hospital_code=_hospital(user), actor=user.username,
                action="trigger_call", entity_id=c.id, meta={"channel": "twilio"})
    db.commit()
    return {"call_id": c.id}