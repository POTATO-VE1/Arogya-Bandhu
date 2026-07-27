"""HMIS / EMR intake router.

Three endpoints, all converting to the same `DischargeEvent` and
feeding the existing enrollment pipeline:

- `POST /api/hmis/discharge-intake`  — universal webhook (HMAC auth)
- `POST /api/hmis/discharge-csv`     — CSV upload (multipart)
- `GET  /api/hmis/sources`           — list supported EMR sources

All three are hospital-scoped (the hospital's EMR pushes to the
right hospital's deployment via the `X-HMIS-Hospital-Code` header
or the `hospital_code` field in the body). Idempotency is via
`emr_patient_id` + `date_of_discharge` — re-pushing the same
discharge is a no-op, not a duplicate.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.hmis import (
    CsvAdapter, DischargeEvent, event_to_intake_kwargs,
    get_adapter, list_sources, sign_body, verify_signature,
)
from app.models import Enrollment, Patient, User

log = logging.getLogger("hmis.router")
router = APIRouter(prefix="/api/hmis", tags=["hmis"])


# ── request shapes ──────────────────────────────────────────────────────────

class DischargeIntakeAck(BaseModel):
    ok: bool
    patient_id: str | None = None
    enrollment_id: str | None = None
    admission_id: str | None = None  # server-side audit log entry id
    emr_source: str
    emr_patient_id: str | None = None
    action: str  # "created" | "duplicate"


# ── helpers ────────────────────────────────────────────────────────────────

def _resolve_hospital(user: User | None, body_hospital_code: str | None) -> str:
    """Pick the right hospital_code for this intake.

    - superadmin: uses body hospital_code, or the default.
    - other roles: locked to their own hospital_code (defence in depth).
    - webhook (no user): uses the X-HMIS-Hospital-Code header (the
      hospital's IT set it when they configured the push), falling
      back to the default HOSPITAL_CODE.
    """
    if user is not None:
        if user.role == "superadmin":
            return body_hospital_code or settings.HOSPITAL_CODE
        return user.hospital_code
    return body_hospital_code or settings.HOSPITAL_CODE


def _is_duplicate(db: Session, hospital_code: str, emr_patient_id: str | None,
                  date_of_discharge: str) -> Enrollment | None:
    """Idempotency: if the same EMR patient + discharge date has
    already been ingested, return the existing enrollment so the
    caller can ack with `action=duplicate` instead of creating a
    second one."""
    if not emr_patient_id:
        return None
    # Find via Patient.created_by-not-reliable; we use a side-table
    # note in the audit_log: search for an `enroll` action by
    # emr_patient_id in the meta. Cheap and reuses existing infra.
    from app.models import AuditLog
    import json as _json
    rows = (db.query(AuditLog)
            .filter(AuditLog.action == "enroll",
                    AuditLog.hospital_code == hospital_code).all())
    for r in rows:
        try:
            meta = _json.loads(r.meta or "{}")
        except Exception:
            continue
        if (meta.get("emr_patient_id") == emr_patient_id
                and meta.get("date_of_discharge") == date_of_discharge):
            return db.query(Enrollment).filter(
                Enrollment.id == r.entity_id).first()
    return None


def _create_enrollment_from_event(db: Session, user: User | None,
                                 hospital_code: str,
                                 ev: DischargeEvent) -> tuple[Patient, Enrollment]:
    """Create the patient + enrollment from a DischargeEvent, reusing
    the same logic the manual intake form does. Idempotency is
    handled by the caller (`_is_duplicate`).

    Service-driven intakes (no logged-in user) use the `_system`
    user as `created_by` so the FK constraint is satisfied.
    """
    from datetime import datetime, timezone
    from app.models import EnrollmentMed, FollowupCall
    from app.protocol_loader import get_protocol
    from app.tzutil import schedule_at_ist_10

    if user is not None:
        created_by = user.id
    else:
        sys_user = db.query(User).filter(User.username == "_system").first()
        if not sys_user:
            # edge case: lifespan's _seed_admin didn't run (e.g. tests
            # without a lifespan). Create the system user on demand.
            from app.security import hash_password
            sys_user = User(
                hospital_code=hospital_code,
                username="_system",
                password_hash=hash_password(secrets.token_hex(32)),
                display_name="System (service)",
                role="staff",
            )
            db.add(sys_user)
            db.flush()
        created_by = sys_user.id

    # Patient
    p = Patient(
        hospital_code=hospital_code,
        name=ev.patient_name,
        age=ev.age,
        sex=(ev.sex or "O")[:1].upper() if ev.sex else None,
        abha_number=ev.abha_number,
        caregiver_name=ev.caregiver_name,
        caregiver_phone=ev.caregiver_phone,
        consent_at=datetime.now(timezone.utc).isoformat(),
        created_by=created_by,
    )
    db.add(p)
    db.flush()

    # Enrollment + meds
    proto_id = ev.protocol_id or "wound_care"
    e = Enrollment(
        hospital_code=hospital_code,
        patient_id=p.id,
        protocol_id=proto_id,
        condition_label=ev.diagnosis_at_discharge,
        ward=ev.ward_name,
        discharge_date=ev.date_of_discharge,
        status="active",
        number_verified=0,  # HMIS push doesn't verify the phone yet
        created_by=created_by,
    )
    db.add(e)
    db.flush()

    for med in (ev.medications or []):
        m = EnrollmentMed(
            enrollment_id=e.id,
            med_name=med.get("name") or med.get("med_name") or "Unknown",
            med_type=med.get("type") or med.get("med_type") or "other",
            doses_per_day=int(med.get("doses_per_day", 3)),
        )
        db.add(m)

    # Followup calls at protocol's schedule_days
    try:
        proto = get_protocol(proto_id)
        schedule = proto.get("schedule_days", [1, 3, 7, 14])
    except Exception:
        schedule = [1, 3, 7, 14]
    for day in schedule:
        scheduled = schedule_at_ist_10(ev.date_of_discharge, day)
        db.add(FollowupCall(
            hospital_code=hospital_code,
            enrollment_id=e.id,
            day_index=day,
            scheduled_at=scheduled,
            kind="followup",
            status="pending",
        ))
    db.flush()
    return p, e


# ── 1. Universal webhook ────────────────────────────────────────────────────

@router.post("/discharge-intake", response_model=DischargeIntakeAck)
async def discharge_intake(
    request: Request,
    x_hmis_signature: str | None = Header(default=None, alias="X-HMIS-Signature"),
    x_hmis_hospital_code: str | None = Header(default=None, alias="X-HMIS-Hospital-Code"),
    x_hmis_source: str | None = Header(default=None, alias="X-HMIS-Source"),
    db: Session = Depends(get_db),
):
    """Universal push endpoint. Any hospital that can do an HTTP POST
    can use this; the body is the canonical DischargeEvent (JSON).

    Auth: HMAC-SHA256(secret, body) in `X-HMIS-Signature`. The
    secret is the deploy's `HMIS_SHARED_SECRET` env var; the
    hospital's IT computes the same and sends the hex digest.

    Idempotency: if the same `emr_patient_id + date_of_discharge`
    is pushed twice, the second call returns `action=duplicate`
    with the existing enrollment_id, not a new patient.
    """
    body_bytes = await request.body()
    secret = settings.HMIS_SHARED_SECRET
    if not secret:
        raise HTTPException(503, "HMIS webhook not configured: set "
                                 "HMIS_SHARED_SECRET in the environment")
    if not x_hmis_signature or not verify_signature(secret, body_bytes, x_hmis_signature):
        raise HTTPException(401, "invalid or missing X-HMIS-Signature")
    try:
        raw = json.loads(body_bytes)
    except json.JSONDecodeError as ex:
        raise HTTPException(400, f"invalid JSON: {ex}")

    # The hospital's EMR may push in its native shape; the X-HMIS-Source
    # header tells us which adapter to use. Default = "custom" (canonical).
    emr_source = (x_hmis_source or raw.get("emr_source") or "custom").lower()
    adapter = get_adapter(emr_source)
    try:
        ev = adapter.to_event(raw)
    except (ValueError, TypeError) as ex:
        # the adapter's validation error is a 400 the hospital can fix
        log.warning("hmis intake: %s rejected: %s", emr_source, ex)
        raise HTTPException(400, f"validation failed: {ex}")

    hospital_code = _resolve_hospital(None, ev.hospital_code or x_hmis_hospital_code)
    existing = _is_duplicate(db, hospital_code, ev.emr_patient_id, ev.date_of_discharge)
    if existing:
        ack = DischargeIntakeAck(
            ok=True, enrollment_id=existing.id,
            patient_id=existing.patient_id,
            emr_source=emr_source, emr_patient_id=ev.emr_patient_id,
            action="duplicate",
        )
        write_audit(db, hospital_code=hospital_code, actor="hmis",
                    action="hmis_intake_duplicate", entity_id=existing.id,
                    meta={"emr_source": emr_source,
                          "emr_patient_id": ev.emr_patient_id})
        db.commit()
        return ack

    p, e = _create_enrollment_from_event(db, None, hospital_code, ev)
    write_audit(db, hospital_code=hospital_code, actor="hmis",
                action="enroll", entity_id=e.id, meta={
                    "source": "hmis_webhook", "emr_source": emr_source,
                    "emr_patient_id": ev.emr_patient_id,
                    "date_of_discharge": ev.date_of_discharge,
                })
    db.commit()
    log.info("hmis intake: created patient=%s enrollment=%s via %s",
             p.id, e.id, emr_source)
    return DischargeIntakeAck(
        ok=True, patient_id=p.id, enrollment_id=e.id,
        emr_source=emr_source, emr_patient_id=ev.emr_patient_id,
        action="created",
    )


# ── 2. CSV upload (hospitals without APIs) ──────────────────────────────────

@router.post("/discharge-csv", response_model=DischargeIntakeAck)
async def discharge_csv(
    file: UploadFile = File(...),
    emr_source: str = Form(default="csv_upload"),
    hospital_code: str = Form(default=""),
    mapping: str = Form(default=""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Upload a CSV of discharge records. The same column mapping
    UI used by the bulk import wizard applies here; we accept it as
    a JSON string in the `mapping` form field.

    Same idempotency as the webhook (emr_patient_id + date_of_discharge).
    """
    if file.content_type not in ("text/csv", "application/csv",
                                  "application/vnd.ms-excel",
                                  "application/octet-stream"):
        raise HTTPException(415, "expected a CSV file")
    text = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(400, "CSV is empty")
    # Apply mapping if provided
    mapping_dict: dict[str, str] = {}
    if mapping:
        try:
            mapping_dict = json.loads(mapping)
        except json.JSONDecodeError as ex:
            raise HTTPException(400, f"invalid mapping JSON: {ex}")

    adapter = CsvAdapter()
    hc = _resolve_hospital(user, hospital_code)
    created = 0
    duplicates = 0
    errors: list[dict] = []
    for i, row in enumerate(rows):
        try:
            ev = adapter.to_event(row, mapping_dict)
        except (ValueError, TypeError) as ex:
            errors.append({"row": i, "reason": str(ex)})
            continue
        existing = _is_duplicate(db, hc, ev.emr_patient_id, ev.date_of_discharge)
        if existing:
            duplicates += 1
            continue
        p, e = _create_enrollment_from_event(db, user, hc, ev)
        write_audit(db, hospital_code=hc, actor=user.username,
                    action="enroll", entity_id=e.id, meta={
                        "source": "hmis_csv", "emr_source": emr_source,
                        "emr_patient_id": ev.emr_patient_id,
                        "date_of_discharge": ev.date_of_discharge,
                    })
        created += 1
    db.commit()
    # Bulk CSV returns a single ack for the whole batch
    return DischargeIntakeAck(
        ok=True, emr_source=emr_source, emr_patient_id=None,
        action=f"created {created}, duplicates {duplicates}, errors {len(errors)}",
    )


# ── 3. List supported sources ──────────────────────────────────────────────

@router.get("/sources")
def sources(_user: User = Depends(current_user)):
    """For the UI: list every supported EMR source + a one-liner."""
    return list_sources()


# ── 4. Source introspection: "I use Medmantra, where do I start?" ─────────

@router.get("/sources/{source}/contract")
def source_contract(source: str, _user: User = Depends(current_user)):
    """Return the contract for one source. The hospital's IT reads
    this to wire their EMR."""
    if source not in ("nic_hmis", "e_hospital", "medmantra", "custom", "csv_upload"):
        raise HTTPException(404, "unknown source")
    if source == "custom":
        return {
            "source": "custom",
            "method": "POST https://<host>/api/hmis/discharge-intake",
            "auth": "X-HMIS-Signature: HMAC-SHA256(HMIS_SHARED_SECRET, body) hex digest",
            "body": DischargeEvent.model_json_schema(),
            "headers": {
                "Content-Type": "application/json",
                "X-HMIS-Signature": "<hex>",
                "X-HMIS-Hospital-Code": "<our hospital code (multi-tenant)>",
                "X-HMIS-Source": "custom",
            },
        }
    if source == "csv_upload":
        return {
            "source": "csv_upload",
            "method": "POST https://<host>/api/hmis/discharge-csv (multipart)",
            "fields": {
                "file": "the CSV file (Content-Type text/csv)",
                "emr_source": "csv_upload (or the source name if you know it)",
                "hospital_code": "our hospital code (admin / superadmin only)",
                "mapping": "JSON dict: {canonical_field: csv_column_name} (optional)",
            },
            "body": DischargeEvent.model_json_schema(),
        }
    # Per-EMR adapters
    samples = {
        "nic_hmis": {
            "field_names_used": [
                "patient_name / Patient Name / NAME",
                "age / Age / AGE_YEARS",
                "sex / Sex / GENDER",
                "mobile_no / Mobile No / MOBILE",
                "date_of_discharge / Date of Discharge / DISCHARGE_DATE",
                "ward_name / Ward Name / WARD",
                "diagnosis_at_discharge / DISCHARGE_DIAGNOSIS",
                "abha_number / ABHA / HEALTH_ID",
                "patient_id / Patient ID / MR_NO / MRN",
            ],
            "method": "POST to /api/hmis/discharge-intake with header X-HMIS-Source: nic_hmis",
            "auth": "HMAC as above",
        },
        "e_hospital": {
            "field_names_used": [
                "PATIENT_NAME", "AGE", "GENDER", "MOBILE_NO",
                "DISCHARGE_DATE", "WARD_NAME", "DISCHARGE_DIAGNOSIS",
                "ABHA_NO", "CR_NO",
            ],
            "method": "POST to /api/hmis/discharge-intake with header X-HMIS-Source: e_hospital",
            "auth": "HMAC as above",
        },
        "medmantra": {
            "field_names_used": [
                "patientName", "age", "gender", "contactNumber",
                "dischargeDate", "ward", "primaryDiagnosis",
                "abha", "uhid", "medications",
            ],
            "method": "POST to /api/hmis/discharge-intake with header X-HMIS-Source: medmantra",
            "auth": "HMAC as above",
        },
    }
    return {"source": source, **samples.get(source, {})}


# ── 5. Sign helper (for the hospital's IT to copy-paste) ──────────────────

@router.get("/sign-helper")
def sign_helper(
    body: str = "",
    _user: User = Depends(current_user),
):
    """Given a sample body, return the X-HMIS-Signature value the
    hospital's IT should send. Pure helper for the contract docs;
    exposes nothing sensitive (the secret is server-side, never sent
    in the response)."""
    secret = settings.HMIS_SHARED_SECRET
    if not secret:
        raise HTTPException(503, "HMIS_SHARED_SECRET not configured")
    sig = sign_body(secret, body.encode())
    return {"signature": sig, "header": f"X-HMIS-Signature: {sig}",
            "note": "compute this same way in your EMR's outbound HTTP config"}
