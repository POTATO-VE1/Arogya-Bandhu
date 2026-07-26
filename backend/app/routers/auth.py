from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.models import User
from app.security import check_rate, record_failure, record_success, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    display_name: str
    role: str


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if not check_rate(ip, body.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "too many attempts, try later")

    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        record_failure(ip, body.username)
        hc = user.hospital_code if user else settings.HOSPITAL_CODE
        write_audit(db, hospital_code=hc, actor=body.username, action="login_failed")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials")

    record_success(ip, body.username)
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    request.session["hospital_code"] = user.hospital_code
    write_audit(db, hospital_code=user.hospital_code, actor=user.username,
                action="login", entity_id=user.id)
    db.commit()
    return UserOut(id=user.id, display_name=user.display_name, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "display_name": user.display_name,
        "role": user.role,
        "hospital_name": settings.HOSPITAL_NAME,
    }