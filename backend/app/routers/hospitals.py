"""Hospital management (superadmin only).

- `GET    /api/hospitals`               — list all hospitals + per-hospital summary
- `POST   /api/hospitals`               — create a new hospital
- `PATCH  /api/hospitals/{code}`        — update name / district / state / contact / active
- `POST   /api/hospitals/{code}/admin`  — promote a user to hospital admin
- `GET    /api/hospitals/{code}/summary`— same shape as the admin dashboard,
                                          scoped to one hospital

All endpoints require role == "superadmin". The `root` user is the only
superadmin (seeded at startup if SUPERADMIN_PASSWORD is set).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import current_user, require_superadmin
from app.models import (
    Enrollment, Escalation, FollowupCall, Hospital, User,
)
from app.security import hash_password

router = APIRouter(prefix="/api/hospitals", tags=["hospitals"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class HospitalIn(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=20)


class HospitalPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    contact_phone: str | None = Field(default=None, max_length=20)
    active: int | None = Field(default=None, ge=0, le=1)


class CreateAdminIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-z][a-z0-9._-]+$")
    display_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=100)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
def list_hospitals(user: User = Depends(require_superadmin),
                   db: Session = Depends(get_db)):
    rows = []
    for h in db.query(Hospital).order_by(Hospital.code).all():
        enrolled = db.query(Enrollment).filter(
            Enrollment.hospital_code == h.code).count()
        active = db.query(Enrollment).filter(
            Enrollment.hospital_code == h.code,
            Enrollment.status == "active").count()
        open_esc = db.query(Escalation).filter(
            Escalation.hospital_code == h.code,
            Escalation.status == "open").count()
        staff = db.query(User).filter(
            User.hospital_code == h.code).count()
        doctors = db.query(User).filter(
            User.hospital_code == h.code, User.role == "doctor").count()
        calls_today = db.query(FollowupCall).filter(
            FollowupCall.hospital_code == h.code,
            FollowupCall.scheduled_at >= datetime.now(timezone.utc)
                .replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        ).count()
        rows.append({
            "code": h.code,
            "name": h.name,
            "district": h.district,
            "state": h.state,
            "contact_phone": h.contact_phone,
            "active": bool(h.active),
            "enrolled_total": enrolled,
            "enrolled_active": active,
            "open_escalations": open_esc,
            "staff_count": staff,
            "doctor_count": doctors,
            "calls_today": calls_today,
            "created_at": h.created_at,
        })
    return rows


@router.post("", status_code=201)
def create_hospital(body: HospitalIn,
                    user: User = Depends(require_superadmin),
                    db: Session = Depends(get_db)):
    existing = db.query(Hospital).filter(Hospital.code == body.code).first()
    if existing:
        raise HTTPException(409, f"Hospital with code '{body.code}' already exists")
    h = Hospital(
        code=body.code,
        name=body.name,
        district=body.district,
        state=body.state,
        contact_phone=body.contact_phone,
        active=1,
    )
    db.add(h)
    db.commit()
    write_audit(db, hospital_code="*", actor=user.username,
                action="create_hospital", entity_id=h.code,
                meta={"name": body.name, "district": body.district})
    db.commit()
    return {"code": h.code, "name": h.name, "active": bool(h.active)}


@router.patch("/{code}")
def patch_hospital(code: str, body: HospitalPatch,
                   user: User = Depends(require_superadmin),
                   db: Session = Depends(get_db)):
    h = db.query(Hospital).filter(Hospital.code == code).first()
    if not h:
        raise HTTPException(404, "hospital not found")
    before = {"name": h.name, "active": h.active}
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(h, k, v)
    db.commit()
    write_audit(db, hospital_code="*", actor=user.username,
                action="update_hospital", entity_id=h.code,
                meta={"before": before, "after": body.model_dump(exclude_unset=True)})
    db.commit()
    return {"code": h.code, "name": h.name, "active": bool(h.active)}


@router.post("/{code}/admin", status_code=201)
def create_hospital_admin(code: str, body: CreateAdminIn,
                          user: User = Depends(require_superadmin),
                          db: Session = Depends(get_db)):
    """Promote a user (or create) as the per-hospital admin. They will be
    able to log in to the dashboard and manage everything within the
    hospital, but won't see data from other hospitals."""
    h = db.query(Hospital).filter(Hospital.code == code).first()
    if not h:
        raise HTTPException(404, "hospital not found")
    username = body.username.lower()
    if not re.match(r"^[a-z][a-z0-9._-]{2,49}$", username):
        raise HTTPException(400, "invalid username")
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(409, "username already taken")
    u = User(
        hospital_code=code,
        username=username,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
        role="admin",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    write_audit(db, hospital_code=code, actor=user.username,
                action="create_hospital_admin", entity_id=u.id,
                meta={"username": username, "display_name": body.display_name})
    db.commit()
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "role": u.role, "hospital_code": u.hospital_code,
    }


@router.get("/{code}/summary")
def hospital_summary(code: str,
                     user: User = Depends(require_superadmin),
                     db: Session = Depends(get_db)):
    """Per-hospital summary, superadmin view. Reuses the same shape as
    the regular admin dashboard but always scoped to `code`."""
    from app.routers.dashboard import overview as _overview
    # We can't easily inject a different `hospital` query param without
    # duplicating logic, so we re-implement the small subset here.
    h = db.query(Hospital).filter(Hospital.code == code).first()
    if not h:
        raise HTTPException(404, "hospital not found")
    # Delegate by calling the overview endpoint with the hospital param
    # would create a circular dep — so just return the hospital card
    # plus the main KPIs.
    from app.models import AuditLog
    return {
        "hospital": {
            "code": h.code, "name": h.name,
            "district": h.district, "state": h.state,
            "active": bool(h.active),
            "created_at": h.created_at,
        },
        "kpis": {
            "enrolled_total": db.query(Enrollment).filter(
                Enrollment.hospital_code == code).count(),
            "enrolled_active": db.query(Enrollment).filter(
                Enrollment.hospital_code == code,
                Enrollment.status == "active").count(),
            "enrolled_completed": db.query(Enrollment).filter(
                Enrollment.hospital_code == code,
                Enrollment.status == "completed").count(),
            "open_escalations": db.query(Escalation).filter(
                Escalation.hospital_code == code,
                Escalation.status == "open").count(),
            "resolved_escalations": db.query(Escalation).filter(
                Escalation.hospital_code == code,
                Escalation.status == "resolved").count(),
            "calls_total": db.query(FollowupCall).filter(
                FollowupCall.hospital_code == code).count(),
            "calls_today": db.query(FollowupCall).filter(
                FollowupCall.hospital_code == code,
                FollowupCall.scheduled_at >= datetime.now(timezone.utc)
                    .replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            ).count(),
            "staff_count": db.query(User).filter(
                User.hospital_code == code).count(),
        },
        "hint": "For the full breakdown (wards, protocols, trend, etc.) "
                "log in as that hospital's admin — they get the same "
                "dashboard scoped to their hospital.",
    }
