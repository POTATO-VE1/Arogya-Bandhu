from sqlalchemy import ForeignKey, String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, now_utc


def _uid() -> str:
    import uuid

    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="staff")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sex: Mapped[str | None] = mapped_column(Text, nullable=True)
    abha_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    caregiver_name: Mapped[str] = mapped_column(Text, nullable=False)
    caregiver_phone: Mapped[str] = mapped_column(Text, nullable=False)
    consent_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)

    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="patient")


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    patient_id: Mapped[str] = mapped_column(Text, ForeignKey("patients.id"), nullable=False)
    protocol_id: Mapped[str] = mapped_column(Text, nullable=False)
    condition_label: Mapped[str] = mapped_column(Text, nullable=False)
    ward: Mapped[str | None] = mapped_column(Text, nullable=True)
    discharge_date: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    number_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sheet_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)

    patient: Mapped["Patient"] = relationship(back_populates="enrollments")
    meds: Mapped[list["EnrollmentMed"]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan"
    )
    calls: Mapped[list["FollowupCall"]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan"
    )


class EnrollmentMed(Base):
    __tablename__ = "enrollment_meds"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    enrollment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("enrollments.id"), nullable=False
    )
    med_name: Mapped[str] = mapped_column(Text, nullable=False)
    med_type: Mapped[str] = mapped_column(Text, nullable=False, default="other")
    aware_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    course_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doses_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    enrollment: Mapped["Enrollment"] = relationship(back_populates="meds")


class FollowupCall(Base):
    __tablename__ = "followup_calls"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    enrollment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("enrollments.id"), nullable=False
    )
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="followup")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_call_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_node: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    enrollment: Mapped["Enrollment"] = relationship(back_populates="calls")
    responses: Mapped[list["CallResponse"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )


class CallResponse(Base):
    __tablename__ = "call_responses"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    call_id: Mapped[str] = mapped_column(
        Text, ForeignKey("followup_calls.id"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    digit: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    answered_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)

    call: Mapped["FollowupCall"] = relationship(back_populates="responses")


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    enrollment_id: Mapped[str] = mapped_column(
        Text, ForeignKey("enrollments.id"), nullable=False
    )
    call_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("followup_calls.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(Text, nullable=False, default="red")
    reasons: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    acked_by: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.id"), nullable=True
    )
    acked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)

    enrollment: Mapped["Enrollment"] = relationship(back_populates="escalations")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)