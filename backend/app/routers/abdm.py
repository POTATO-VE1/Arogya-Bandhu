"""ABDM router — ABHA verify + discharge push to ABDM.

Two endpoints:
- POST /api/abdm/verify-abha       — request OTP, then verify
- POST /api/abdm/push-discharge    — push a FHIR bundle to ABDM

Both use the `AbdmClient` from `app.abdm` — env-driven between
`MockAbdmClient` (default) and `RealAbdmClient` (sandbox/prod).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.abdm import get_abdm
from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import current_user
from app.fhir import build_bundle
from app.models import Enrollment, User
from sqlalchemy.orm import Session

log = logging.getLogger("abdm.router")
router = APIRouter(prefix="/api/abdm", tags=["abdm"])


# ── request / response shapes ──────────────────────────────────────────────

class VerifyAbhaIn(BaseModel):
    abha_number: str = Field(min_length=14, max_length=20,
                             description="The 14-digit ABHA, with or without hyphens")
    otp: str | None = Field(default=None, min_length=4, max_length=10,
                            description="The OTP. Omit on the first call to request it.")
    txn_id: str | None = Field(default=None,
                               description="The ABDM transaction id from the previous "
                                           "request-otp call. Omit on the first call.")
    enrollment_id: str | None = Field(default=None,
                                      description="If the patient is being enrolled, "
                                                  "the enrollment id to attach the verified "
                                                  "ABHA to.")


class VerifyAbhaOut(BaseModel):
    verified: bool
    abha_number: str
    name: str | None = None
    txn_id: str | None = None
    reason: str | None = None
    mocked: bool = False
    otp_for_demo: str | None = Field(default=None,
        description="Only present in mock mode; tells the demo which OTP to enter")


class PushDischargeIn(BaseModel):
    enrollment_id: str = Field(min_length=1)
    consented: bool = Field(default=True,
        description="Patient must have given explicit consent to push to ABDM")


class PushDischargeOut(BaseModel):
    accepted: bool
    request_id: str | None = None
    reason: str | None = None
    mocked: bool = False
    mock_outbox_path: str | None = None
    bundle_summary: dict[str, Any] | None = None


# ── /verify-abha ───────────────────────────────────────────────────────────

@router.post("/verify-abha", response_model=VerifyAbhaOut)
def verify_abha(body: VerifyAbhaIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Two-step: first call sends `otp=None` to request the OTP.
    Second call sends the OTP + txn_id from the first call.

    Mock mode: returns the OTP in the response (so the demo can show
    "enter OTP 123456"). Real mode: the OTP is sent to the patient's
    Aadhaar-linked phone; the API never sees it again.
    """
    client = get_abdm()
    if client is None:
        raise HTTPException(503, "ABDM not configured (set ABDM_MODE=mock or ABDM_MODE=real with creds)")
    is_mock = client.__class__.__name__ == "MockAbdmClient"

    # Step 1: no OTP yet → request it
    if not body.otp:
        try:
            txn_id = client.request_otp(body.abha_number)
        except Exception as ex:
            log.warning("ABDM request_otp failed: %s", ex)
            raise HTTPException(502, f"ABDM request-otp failed: {ex}")
        write_audit(db, hospital_code=user.hospital_code, actor=user.username,
                    action="abdm_request_otp", entity_id=None,
                    meta={"abha_prefix": body.abha_number[:6],
                          "mocked": is_mock})
        db.commit()
        out = VerifyAbhaOut(verified=False, abha_number=body.abha_number,
                            txn_id=txn_id, mocked=is_mock)
        # In mock mode, return the OTP the user should enter. NEVER
        # returned in real mode (the OTP is sent to the phone, not the
        # API response).
        if is_mock:
            from app.abdm import _MOCK_SEEDS
            seed = _MOCK_SEEDS.get(body.abha_number)
            if seed:
                out.otp_for_demo = seed["otp"]
        return out

    # Step 2: verify the OTP
    if not body.txn_id:
        raise HTTPException(400, "txn_id required when verifying an OTP")
    try:
        result = client.verify_abha(body.abha_number, body.otp, body.txn_id)
    except Exception as ex:
        log.warning("ABDM verify_abha failed: %s", ex)
        raise HTTPException(502, f"ABDM verify-abha failed: {ex}")

    if result.verified and body.enrollment_id:
        # Attach the verified ABHA to the enrollment's patient.
        e = db.query(Enrollment).filter(
            Enrollment.id == body.enrollment_id,
            Enrollment.hospital_code == user.hospital_code).first()
        if e:
            from app.models import Patient
            p = db.get(Patient, e.patient_id)
            if p:
                p.abha_number = body.abha_number
                p.abha_verified = 1
                p.abha_verified_at = now_utc()
                db.commit()
    write_audit(db, hospital_code=user.hospital_code, actor=user.username,
                action="abha_verified", entity_id=body.enrollment_id,
                meta={"abha_prefix": body.abha_number[:6],
                      "mocked": is_mock, "verified": result.verified,
                      "name": result.name})
    db.commit()
    return VerifyAbhaOut(
        verified=result.verified, abha_number=body.abha_number,
        name=result.name, txn_id=body.txn_id,
        reason=result.reason, mocked=is_mock,
    )


# ── /push-discharge ────────────────────────────────────────────────────────

@router.post("/push-discharge", response_model=PushDischargeOut)
def push_discharge(body: PushDischargeIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """Build the FHIR DischargeSummaryRecord for the given enrollment
    and push it to ABDM. In mock mode, the bundle is written to
    /tmp/abdm_outbox.jsonl so the demo can show what was sent."""
    if not body.consented:
        raise HTTPException(400, "patient must have given explicit consent to push to ABDM")
    e = db.query(Enrollment).filter(
        Enrollment.id == body.enrollment_id,
        Enrollment.hospital_code == user.hospital_code).first()
    if not e:
        raise HTTPException(404, "enrollment not found")
    from app.models import EnrollmentMed, Patient
    p = db.get(Patient, e.patient_id)
    meds = db.query(EnrollmentMed).filter(
        EnrollmentMed.enrollment_id == e.id).all()
    if not p:
        raise HTTPException(404, "patient not found")
    bundle = build_bundle(p, e, meds)

    client = get_abdm()
    if client is None:
        raise HTTPException(503, "ABDM not configured")
    is_mock = client.__class__.__name__ == "MockAbdmClient"
    try:
        result = client.push_discharge(bundle)
    except Exception as ex:
        log.warning("ABDM push_discharge failed: %s", ex)
        raise HTTPException(502, f"ABDM push failed: {ex}")
    write_audit(db, hospital_code=user.hospital_code, actor=user.username,
                action="abdm_push_discharge", entity_id=e.id,
                meta={"accepted": result.accepted, "mocked": is_mock,
                      "request_id": result.request_id,
                      "n_resources": len(bundle.get("entry", []))})
    db.commit()

    # Compute a tiny summary for the response so the UI can show
    # "pushed DischargeSummaryRecord with N resources" without a
    # separate fetch.
    entries = bundle.get("entry", []) or []
    n_patient = sum(1 for e_ in entries
                    if isinstance(e_, dict) and e_.get("resource", {}).get("resourceType") == "Patient")
    n_med = sum(1 for e_ in entries
                if isinstance(e_, dict) and e_.get("resource", {}).get("resourceType") == "MedicationRequest")
    return PushDischargeOut(
        accepted=result.accepted, request_id=result.request_id,
        reason=result.reason, mocked=is_mock,
        mock_outbox_path=result.mock_outbox_path,
        bundle_summary={"n_resources": len(entries),
                        "n_patient": n_patient, "n_medication": n_med,
                        "profile": bundle.get("entry", [{}])[0]
                            .get("resource", {}).get("meta", {}).get("profile", ["?"])[0]},
    )


# ── /status ────────────────────────────────────────────────────────────────

@router.get("/status")
def status(_user: User = Depends(current_user)):
    """For the UI: report which ABDM mode is active + (in mock) the
    list of demo ABHAs the user can try."""
    from app.abdm import _MOCK_SEEDS
    from app.config import settings
    client = get_abdm()
    is_mock = client is not None and client.__class__.__name__ == "MockAbdmClient"
    return {
        "mode": settings.ABDM_MODE,
        "base_url": settings.ABDM_BASE_URL,
        "configured": client is not None,
        "mocked": is_mock,
        "demo_seeds": (sorted(_MOCK_SEEDS.keys()) if is_mock else []),
        "hint": ("in mock mode, enter the OTP shown in the previous response"
                 if is_mock else "OTPs are sent to the patient's Aadhaar-linked phone"),
    }
