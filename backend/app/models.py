from sqlalchemy import ForeignKey, String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, now_utc


def _uid() -> str:
    import uuid

    return uuid.uuid4().hex


class Hospital(Base):
    """A first-class hospital entity. A single deploy can serve many hospitals
    in one SQLite file, each with their own staff, patients, escalations.

    The hospital_code is the existing tenant discriminator on every
    patient/enrollment/call row — backwards compatible (no migration needed
    for existing data; the seed step backfills a default row).
    """
    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="staff")
    ward: Mapped[str | None] = mapped_column(Text, nullable=True)  # ward assignment for nurse/staff
    telegram_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # for direct DM alerts
    supervisor: Mapped[str | None] = mapped_column(Text, nullable=True)  # supervisor username
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sex: Mapped[str | None] = mapped_column(Text, nullable=True)
    abha_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ABHA verification status (set when /api/abdm/verify-abha returns
    # verified=True). Used by the FHIR export (verifiable provenance)
    # and by the consent trail.
    abha_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abha_verified_at: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id"), nullable=True)
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
    # Which Twilio account placed the call (multi-account rotation). NULL
    # = scheduler hasn't placed it yet, or single-account legacy.
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(Text, ForeignKey("users.id"), nullable=True)  # null = system/scheduler

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


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_id: Mapped[str | None] = mapped_column(Text, ForeignKey("patients.id"), nullable=True)
    is_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_staff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_admin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auth_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_lang: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    diet_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    medication_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    feeling_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_fit_consent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)


class PendingNotification(Base):
    """T12 (docs/09_PLAN.md): DB-backed retry queue for escalation
    notifications. Telegram is the primary channel; if the send fails, the
    scheduler retries the send every 5 min for up to 5 attempts. After 5
    failures the row is marked `failed` and an SSE `notification:failed`
    event tells the dashboard."""
    __tablename__ = "pending_notifications"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'escalation'
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)  # escalation_id
    text: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # 'pending' | 'sent' | 'failed'
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)
    sent_at: Mapped[str | None] = mapped_column(Text, nullable=True)