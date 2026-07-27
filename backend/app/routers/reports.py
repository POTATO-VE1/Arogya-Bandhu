"""Patient report upload & listing (intake file picker backend)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import current_user
from app.health_fit import PatientReport
from app.models import Enrollment, Patient, User

router = APIRouter(tags=["reports"])

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Storage root — backend/data/reports/
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"


def _ext(filename: str | None) -> str:
    """Return lowercase file extension (with dot), or ''."""
    if not filename:
        return ""
    return Path(filename).suffix.lower()


def _safe_name(filename: str | None) -> str:
    """Strip path traversal; keep only the basename."""
    if not filename:
        return "upload"
    return Path(filename).name


def _report_type(ext: str) -> str:
    """Derive report_type label from file extension."""
    return ext.lstrip(".") or "unknown"


def _resolve_enrollment(
    eid: str, user: User, db: Session,
) -> tuple[Enrollment, Patient]:
    """Load enrollment + patient, enforce hospital_code authz. Raise 404 on miss."""
    e = db.query(Enrollment).filter(
        Enrollment.id == eid,
        Enrollment.hospital_code == user.hospital_code,
    ).first()
    if not e:
        raise HTTPException(404, "enrollment not found")
    p = db.query(Patient).filter(Patient.id == e.patient_id).first()
    if not p:
        raise HTTPException(404, "patient not found")
    return e, p


# ── POST /api/enrollments/{eid}/reports ──────────────────────────────────────
@router.post("/api/enrollments/{eid}/reports", status_code=201)
async def upload_reports(
    eid: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Upload one or more report files for an enrollment."""
    e, p = _resolve_enrollment(eid, user, db)

    # Ensure storage dir exists
    dest_dir = REPORTS_DIR / eid
    dest_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[dict] = []
    for f in files:
        ext = _ext(f.filename)
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                400,
                f"File '{f.filename}': extension '{ext}' not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                400,
                f"File '{f.filename}': exceeds 10 MB limit",
            )

        safe = _safe_name(f.filename)
        stored_name = f"{uuid.uuid4().hex}_{safe}"
        stored_path = dest_dir / stored_name
        stored_path.write_bytes(content)

        report = PatientReport(
            patient_id=p.id,
            hospital_code=user.hospital_code,
            report_type=_report_type(ext),
            filename=str(stored_path.relative_to(REPORTS_DIR.parent.parent)),
            uploaded_by=user.username,
            extracted_data=None,
            uploaded_at=now_utc(),
        )
        db.add(report)
        db.flush()  # get report.id

        uploaded.append({
            "id": report.id,
            "filename": report.filename,
            "report_type": report.report_type,
            "uploaded_at": report.uploaded_at,
        })

    write_audit(
        db,
        hospital_code=user.hospital_code,
        actor=user.username,
        action="report_upload",
        entity_id=p.id,
        meta={"enrollment_id": eid, "count": len(uploaded)},
    )
    db.commit()

    return {"uploaded": uploaded}


# ── GET /api/enrollments/{eid}/reports ───────────────────────────────────────
@router.get("/api/enrollments/{eid}/reports")
def list_reports(
    eid: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """List all reports attached to an enrollment."""
    e, _p = _resolve_enrollment(eid, user, db)

    rows = (
        db.query(PatientReport)
        .filter(PatientReport.patient_id == e.patient_id)
        .order_by(PatientReport.uploaded_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "report_type": r.report_type,
            "uploaded_at": r.uploaded_at,
            "uploaded_by": r.uploaded_by,
        }
        for r in rows
    ]


# ── GET /api/reports/{report_id}/download ────────────────────────────────────
@router.get("/api/reports/{report_id}/download")
def download_report(
    report_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Serve a report file. Authz: report must belong to session hospital."""
    r = db.query(PatientReport).filter(PatientReport.id == report_id).first()
    if not r or r.hospital_code != user.hospital_code:
        raise HTTPException(404, "report not found")

    file_path = REPORTS_DIR.parent.parent / r.filename
    if not file_path.is_file():
        raise HTTPException(404, "file missing from storage")

    return FileResponse(
        path=str(file_path),
        filename=_safe_name(r.filename),
        media_type="application/octet-stream",
    )
