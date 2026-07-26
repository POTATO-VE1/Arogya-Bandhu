"""SQLAlchemy models for health device integration."""
from sqlalchemy import ForeignKey, String, Integer, Text, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, now_utc


def _uid() -> str:
    import uuid
    return uuid.uuid4().hex


class PatientHealthToken(Base):
    """OAuth tokens for Google Fit (or other providers) per patient."""
    __tablename__ = "patient_health_tokens"
    __table_args__ = (
        UniqueConstraint("patient_id", "provider", name="uq_patient_provider"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(Text, ForeignKey("patients.id"), nullable=False)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="google_fit")
    access_token: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet-encrypted
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet-encrypted
    token_expiry: Mapped[str] = mapped_column(Text, nullable=False)  # UTC ISO-8601
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)
    last_synced_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class PatientHealthData(Base):
    """Time-series health metrics fetched from connected devices."""
    __tablename__ = "patient_health_data"
    __table_args__ = (
        UniqueConstraint("patient_id", "metric_type", "recorded_at", name="uq_patient_metric_time"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(Text, ForeignKey("patients.id"), nullable=False)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    metric_type: Mapped[str] = mapped_column(Text, nullable=False)
        # 'heart_rate' | 'spo2' | 'steps' | 'sleep' | 'body_temp' | 'blood_pressure' | 'weight'
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
        # 'bpm' | '%' | 'count' | 'minutes' | '°C' | 'mmHg' | 'kg'
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)  # when measurement was taken
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)  # device name


class PatientReport(Base):
    """Uploaded medical reports (PDF/image) with extracted data."""
    __tablename__ = "patient_reports"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_uid)
    patient_id: Mapped[str] = mapped_column(Text, ForeignKey("patients.id"), nullable=False)
    hospital_code: Mapped[str] = mapped_column(Text, nullable=False)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
        # 'lab_report' | 'discharge_summary' | 'prescription' | 'other'
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False)  # 'patient' | 'nurse'
    extracted_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON key-value pairs
    uploaded_at: Mapped[str] = mapped_column(Text, nullable=False, default=now_utc)
