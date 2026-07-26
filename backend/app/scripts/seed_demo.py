"""seed_demo.py --reset : clean DB with staff users only, no patients.

Users: 2 doctors + 3 nurses. Patients are imported via CSV during demo.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.db import SessionLocal, create_all, init_engine
from app.config import settings
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation, FollowupCall, Patient, User,
)
from app.security import hash_password

HOSPITAL = settings.HOSPITAL_CODE

USERS = [
    ("dr.priya",   "doctor123", "Dr. Priya Sharma",   "doctor"),
    ("dr.rajesh",  "doctor123", "Dr. Rajesh Kumar",   "doctor"),
    ("nurse01",    "nurse1234", "Nurse Asha",         "staff"),
    ("nurse02",    "nurse1234", "Nurse Kavita",       "staff"),
    ("nurse03",    "nurse1234", "Nurse Lakshmi",      "staff"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    init_engine(settings.DATABASE_URL)
    s = SessionLocal()

    if args.reset:
        for m in [CallResponse, Escalation, FollowupCall, EnrollmentMed, Enrollment, Patient, User]:
            s.query(m).delete()
        s.commit()

    if s.query(User).count() == 0:
        for username, password, display_name, role in USERS:
            s.add(User(
                hospital_code=HOSPITAL,
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                role=role,
            ))
        s.commit()

    s.close()
    print("seed complete · users:")
    for username, password, display_name, role in USERS:
        print(f"  {username}/{password} ({display_name}, {role})")
    print("· no patients — import via CSV")


def seed_health_data():
    """Seed demo health data for existing patients (for demo purposes)."""
    import random
    import uuid
    from datetime import datetime, timedelta, timezone
    from app.health_fit import PatientHealthData, PatientHealthToken
    from app.db import now_utc

    s = SessionLocal()
    patients = s.query(Patient).filter(Patient.hospital_code == HOSPITAL).all()
    if not patients:
        print("no patients found — import patients first")
        s.close()
        return

    now = datetime.now(timezone.utc)
    stored = 0

    for patient in patients[:3]:  # seed for first 3 patients only
        # Check if already has data
        existing = s.query(PatientHealthData).filter(
            PatientHealthData.patient_id == patient.id
        ).count()
        if existing > 0:
            continue

        # Generate 7 days of realistic data
        for day_offset in range(7, 0, -1):
            day = now - timedelta(days=day_offset)

            # Heart rate (60-90, slightly elevated early, improving)
            base_hr = 85 - day_offset  # improving trend
            hr = base_hr + random.randint(-5, 5)
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="heart_rate",
                value=float(hr),
                unit="bpm",
                recorded_at=day.replace(hour=10, minute=0).isoformat(),
                source="demo_seed",
            ))

            # SpO2 (94-99, slightly low early, improving)
            base_spo2 = 94 + (day_offset // 3)
            spo2 = min(99, base_spo2 + random.randint(-1, 1))
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="spo2",
                value=float(spo2),
                unit="%",
                recorded_at=day.replace(hour=10, minute=5).isoformat(),
                source="demo_seed",
            ))

            # Steps (increasing over time — recovery)
            base_steps = 1000 + (7 - day_offset) * 500
            steps = base_steps + random.randint(-200, 200)
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="steps",
                value=float(max(0, steps)),
                unit="count",
                recorded_at=day.replace(hour=18, minute=0).isoformat(),
                source="demo_seed",
            ))

            # Sleep (5-8 hours, improving)
            sleep_h = 5 + (7 - day_offset) * 0.3 + random.uniform(-0.5, 0.5)
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="sleep",
                value=round(sleep_h * 60, 1),  # store as minutes
                unit="minutes",
                recorded_at=day.replace(hour=6, minute=0).isoformat(),
                source="demo_seed",
            ))

            # Body temp (36.5-37.2, no fever)
            temp = 36.8 + random.uniform(-0.3, 0.4)
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="body_temp",
                value=round(temp, 1),
                unit="°C",
                recorded_at=day.replace(hour=10, minute=10).isoformat(),
                source="demo_seed",
            ))

            stored += 5

    s.commit()
    s.close()
    print(f"seeded {stored} health data points for {min(3, len(patients))} patients")


if __name__ == "__main__":
    import sys
    if "--health" in sys.argv:
        init_engine(settings.DATABASE_URL)
        seed_health_data()
    else:
        main()
