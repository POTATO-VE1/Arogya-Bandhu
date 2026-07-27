from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Query, Session

from app.config import settings
from app.db import get_db
from app.models import User


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Admin or superadmin. The two differ in scope: admin is per-hospital,
    superadmin sees across all hospitals (see `require_superadmin`)."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


def require_superadmin(user: User = Depends(current_user)) -> User:
    """Superadmin only — can manage all hospitals, see all metrics, create
    new hospitals. The single deploy is multi-hospital through this role."""
    if user.role != "superadmin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superadmin only")
    return user


# ── Role hierarchy helpers ────────────────────────────────────────────────────

ROLE_HIERARCHY = {"superadmin": 4, "admin": 3, "doctor": 2, "nurse": 1,
                  "staff": 0, "intern": 0}


def is_cross_hospital(user: User) -> bool:
    """True if this user can see data across multiple hospitals
    (superadmin only). Used to skip the hospital_code filter."""
    return user.role == "superadmin"


def hospital_scope(user: User, requested: str | None) -> str | None:
    """Return the hospital_code to filter on, or None for "all hospitals".

    - superadmin: respects `?hospital=CODE`; default = all (None).
    - admin/doctor/nurse/staff: locked to their own hospital_code;
      the `requested` param is ignored (defence in depth).
    """
    if is_cross_hospital(user):
        return requested or None
    return user.hospital_code


def apply_hospital_scope(query: Query, model, user: User,
                          requested: str | None = None) -> Query:
    """Apply the appropriate hospital_code filter to a query.

    - superadmin: no filter, unless `requested` is set (then scope to it).
    - everyone else: filter by their own `user.hospital_code` (the `requested`
      arg is ignored, defence in depth against URL mutation).

    Use as:
        q = apply_hospital_scope(db.query(Enrollment), Enrollment, user)
    """
    if is_cross_hospital(user):
        if requested:
            return query.filter(model.hospital_code == requested)
        return query
    return query.filter(model.hospital_code == user.hospital_code)


def can_access_enrollment(user: User, enrollment_ward: str | None) -> bool:
    """Admin and doctors see all in hospital; nurse/staff see only their ward.
    Superadmin sees all wards across all hospitals."""
    if user.role in ("admin", "doctor", "superadmin"):
        return True
    # nurse/staff: only if enrollment ward matches user's assigned ward
    if not user.ward:
        return True  # no ward assigned = see all (fallback)
    if not enrollment_ward:
        return True  # unassigned enrollment = see all
    return user.ward.lower() == enrollment_ward.lower()


def can_access_patient(user: User, patient_ward: str | None) -> bool:
    """Admin and doctors see all patients in hospital.
    Superadmin sees all patients across all hospitals."""
    if user.role in ("admin", "doctor", "superadmin"):
        return True
    if not user.ward:
        return True
    if not patient_ward:
        return True
    return user.ward.lower() == patient_ward.lower()