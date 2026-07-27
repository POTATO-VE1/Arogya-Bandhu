from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.models import User
from app.security import check_rate, hash_password, record_failure, record_success, verify_password
import hashlib

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    display_name: str
    role: str
    hospital_name: str


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if not check_rate(ip, body.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many failed attempts. Locked for 30s.")

    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        record_failure(ip, body.username)
        hc = user.hospital_code if user else settings.HOSPITAL_CODE
        write_audit(db, hospital_code=hc, actor=body.username, action="login_failed")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    record_success(ip, body.username)
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["hospital_code"] = user.hospital_code
    write_audit(db, hospital_code=user.hospital_code, actor=user.username,
                action="login", entity_id=user.id)
    db.commit()
    return UserOut(
        id=user.id,
        display_name=user.display_name,
        role=user.role,
        hospital_name=settings.HOSPITAL_NAME,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── T10: forgot-password via Telegram (no new dep) ────────────────────────────
# In-memory OTP store, keyed by username. Single-process; document the
# Redis-scale path in the doc string.
import secrets
import time as _time

_otp_store: dict[str, dict] = {}   # username -> {code_hash, expires_at, attempts}
_OTP_TTL_SECONDS = 15 * 60
_OTP_MAX_ATTEMPTS = 5


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class ForgotIn(BaseModel):
    username: str


class ResetIn(BaseModel):
    username: str
    otp: str
    new_password: str = Field(min_length=6, max_length=100)


@router.post("/forgot", status_code=status.HTTP_200_OK)
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Initiate password reset. Always returns 200 (no enumeration).

    Generates a 6-digit OTP, stores a hash, and (if Telegram is configured)
    sends the OTP to the admin's Telegram chat with a structured message.
    The admin relays the OTP verbally to the staff member. This is the
    hackathon-friendly version of per-user Telegram linking.
    """
    user = db.query(User).filter(User.username == body.username).first()
    if user:
        code = f"{secrets.randbelow(10**6):06d}"
        _otp_store[body.username] = {
            "code_hash": _hash_otp(code),
            "expires_at": _time.time() + _OTP_TTL_SECONDS,
            "attempts": 0,
        }
        # Send via Telegram (best-effort; never blocks)
        try:
            from app.notify import telegram_send
            msg = (
                f"Password reset OTP for {body.username}\n"
                f"Code: {code}\n"
                f"Valid for 15 minutes. Relay to the staff member."
            )
            telegram_send(msg)
        except Exception:
            pass
    return {"ok": True, "hint": "If the username exists, an OTP was sent to the admin's Telegram."}


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    """Complete password reset. Validates the OTP, then sets the new password."""
    rec = _otp_store.get(body.username)
    if not rec:
        raise HTTPException(400, "no reset requested for this username")
    if _time.time() > rec["expires_at"]:
        _otp_store.pop(body.username, None)
        raise HTTPException(400, "OTP expired; request a new one")
    rec["attempts"] += 1
    if rec["attempts"] > _OTP_MAX_ATTEMPTS:
        _otp_store.pop(body.username, None)
        raise HTTPException(400, "too many attempts; request a new OTP")
    if _hash_otp(body.otp) != rec["code_hash"]:
        raise HTTPException(401, "invalid OTP")
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(400, "no such user")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    write_audit(db, hospital_code=user.hospital_code, actor=body.username,
                action="password_reset")
    db.commit()
    _otp_store.pop(body.username, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "display_name": user.display_name,
        "role": user.role,
        "hospital_name": settings.HOSPITAL_NAME,
    }