"""Staff Management Router — Admin Only (RBAC Enforced).

Provides production-grade, secure endpoints to list, create, update, and delete
hospital staff accounts. All passwords are securely hashed using OWASP PBKDF2-SHA256.
"""
from __future__ import annotations

import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db, now_utc
from app.deps import require_admin, current_user
from app.models import User
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/staff-mgmt", tags=["staff-mgmt"])


class StaffOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    ward: str | None = None
    hospital_code: str
    created_at: str


class CreateStaffIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    display_name: str = Field(..., min_length=2, max_length=100)
    role: str = Field(..., description="Role: admin, doctor, nurse, staff")
    password: str = Field(..., min_length=6, max_length=100)


class UpdateStaffIn(BaseModel):
    display_name: str | None = Field(None, min_length=2, max_length=100)
    role: str | None = Field(None, description="Role: admin, doctor, nurse, staff")


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=100)


@router.get("", response_model=list[StaffOut])
def list_staff(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """List all staff accounts for the hospital."""
    users = (
        db.query(User)
        .filter(User.hospital_code == admin.hospital_code)
        .order_by(User.created_at.desc())
        .all()
    )
    return [
        StaffOut(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            ward=u.ward,
            hospital_code=u.hospital_code,
            created_at=u.created_at,
        )
        for u in users
    ]


@router.get("/me", response_model=StaffOut)
def get_my_profile(user: User = Depends(current_user)):
    """Get current user's profile."""
    return StaffOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        ward=user.ward,
        hospital_code=user.hospital_code,
        created_at=user.created_at,
    )


@router.post("", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def create_staff(
    body: CreateStaffIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new staff member with PBKDF2-SHA256 hashed password."""
    username = body.username.strip().lower()
    if not re.match(r"^[a-z0-9._-]+$", username):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Username may only contain lowercase letters, numbers, dots, hyphens, and underscores",
        )

    role = body.role.strip().lower()
    if role not in ("admin", "doctor", "nurse", "staff"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid role. Allowed roles: admin, doctor, nurse, staff",
        )

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Username '{username}' already exists"
        )

    new_user = User(
        hospital_code=admin.hospital_code,
        username=username,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
        role=role,
        created_at=now_utc(),
    )
    db.add(new_user)
    db.commit()

    write_audit(
        db,
        hospital_code=admin.hospital_code,
        actor=admin.username,
        action="create_staff",
        entity_id=new_user.id,
        meta={"username": username, "role": role},
    )
    db.commit()

    return StaffOut(
        id=new_user.id,
        username=new_user.username,
        display_name=new_user.display_name,
        role=new_user.role,
        ward=new_user.ward,
        hospital_code=new_user.hospital_code,
        created_at=new_user.created_at,
    )


@router.patch("/{user_id}", response_model=StaffOut)
def update_staff(
    user_id: str,
    body: UpdateStaffIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a staff member's display name or role."""
    target = (
        db.query(User)
        .filter(User.id == user_id, User.hospital_code == admin.hospital_code)
        .first()
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    if body.display_name is not None:
        target.display_name = body.display_name.strip()

    if body.role is not None:
        role = body.role.strip().lower()
        if role not in ("admin", "doctor", "nurse", "staff"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Invalid role. Allowed roles: admin, doctor, nurse, staff",
            )
        target.role = role

    db.commit()

    write_audit(
        db,
        hospital_code=admin.hospital_code,
        actor=admin.username,
        action="update_staff",
        entity_id=user_id,
        meta={"display_name": target.display_name, "role": target.role},
    )
    db.commit()

    return StaffOut(
        id=target.id,
        username=target.username,
        display_name=target.display_name,
        role=target.role,
        ward=target.ward,
        hospital_code=target.hospital_code,
        created_at=target.created_at,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_own_password(
    body: ChangePasswordIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Change current user's own password."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Current password is incorrect"
        )

    if body.current_password == body.new_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "New password must be different from current password",
        )

    user.password_hash = hash_password(body.new_password)
    db.commit()

    write_audit(
        db,
        hospital_code=user.hospital_code,
        actor=user.username,
        action="change_password",
        entity_id=user.id,
    )
    db.commit()


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_staff_password(
    user_id: str,
    body: ChangePasswordIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin resets another user's password (requires current admin password)."""
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Your admin password is incorrect"
        )

    target = (
        db.query(User)
        .filter(User.id == user_id, User.hospital_code == admin.hospital_code)
        .first()
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    if user_id == admin.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Use /api/staff-mgmt/change-password to change your own password",
        )

    target.password_hash = hash_password(body.new_password)
    db.commit()

    write_audit(
        db,
        hospital_code=admin.hospital_code,
        actor=admin.username,
        action="reset_password",
        entity_id=user_id,
        meta={"target_username": target.username},
    )
    db.commit()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_staff(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a staff member. Protects current admin from self-deletion."""
    if user_id == admin.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot delete your own active admin account",
        )

    target = (
        db.query(User)
        .filter(User.id == user_id, User.hospital_code == admin.hospital_code)
        .first()
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")

    username = target.username
    db.delete(target)
    db.commit()

    write_audit(
        db,
        hospital_code=admin.hospital_code,
        actor=admin.username,
        action="delete_staff",
        entity_id=user_id,
        meta={"username": username},
    )
    db.commit()
