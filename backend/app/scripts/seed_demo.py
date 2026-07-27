"""seed_demo.py — wipe + seed a demo-ready hospital board.

Usage:
    python -m app.scripts.seed_demo --reset                # admin only
    python -m app.scripts.seed_demo --reset --with-demo-data  # 30 patients, live calls, escalations

`--with-demo-data` populates:
  - 4 staff (admin, nurse_kavita, nurse_lakshmi, dr_suresh)
  - 30 patients across 3 protocols, varied wards and discharge_dates
  - scheduled followup_calls for each enrollment (Day 1/3/7/14)
  - 8 in-progress sim calls (so the dashboard shows live activity)
  - 1 open RED escalation (visible in SOS banner)
  - 1 acked escalation
  - 1 resolved escalation with a `recovered` outcome
"""
import argparse
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.db import SessionLocal, init_engine
from app.config import settings
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation,
    FollowupCall, Hospital, Patient, PendingNotification,
    TelegramSession, User,
)
from app.security import hash_password

HOSPITAL = settings.HOSPITAL_CODE

# ── demo data ────────────────────────────────────────────────────────────────

_FIRST_F = [
    "Lakshmamma", "Gangamma", "Honnamma", "Parvathamma", "Savithramma",
    "Suma", "Vijaya", "Kamala", "Latha", "Padmavathi", "Rathnamma",
    "Sarojini", "Tulasi", "Vasantha", "Yashoda", "Annapurna",
    "Bhagyashree", "Chandrakala", "Durga", "Fathima", "Girija",
]
_FIRST_M = [
    "Ramesh", "Venkatesh", "Manjunath", "Suresh", "Ganesh", "Mahesh",
    "Rajesh", "Santosh", "Dinesh", "Umesh", "Naveen", "Kiran",
    "Prakash", "Ravi", "Girish", "Vijay", "Anil", "Sunil", "Vinod",
    "Rahul", "Arun", "Ashok", "Basavaraj", "Darshan", "Gopal",
]
_LAST = [
    "Gowda", "Patil", "Shetty", "Hegde", "Kamath", "Rao", "Reddy",
    "Nair", "Menon", "Iyer", "Naik", "Kulkarni", "Desai", "Joshi",
    "Bhat", "Pai", "Prasad", "Murthy", "Swamy", "Kumar", "Devi",
]
_WARDS = ["Ward-1", "Ward-2", "Ward-3", "Ward-4", "Orthopedics", "Cardiology"]

_CONDITIONS = {
    "wound_care": [
        "Post-op appendectomy", "Post-op hernia repair", "C-section wound care",
        "Post-op knee replacement", "Abscess drainage", "Diabetic foot ulcer",
        "Post-op cholecystectomy", "Laceration repair", "Burn wound management",
    ],
    "antibiotic_course": [
        "Lower RTI on Azithromycin", "UTI on Ciprofloxacin", "Wound infection on Cefixime",
        "Cellulitis on Amoxicillin", "Pneumonia on Levofloxacin",
        "Skin infection on Clindamycin", "Pharyngitis on Amoxicillin",
        "Dental infection on Metronidazole",
    ],
    "fever_viral": [
        "Viral fever with cough", "Post-viral fatigue", "Fever with chills",
        "Dengue convalescence", "Chikungunya recovery", "Viral gastroenteritis",
    ],
}

_MEDS = {
    "wound_care": [
        ("Paracetamol 500mg", "other", 3),
        ("Amoxiclav 625mg", "antibiotic", 2),
        ("Ibuprofen 400mg", "other", 2),
        ("Cefuroxime 500mg", "antibiotic", 2),
    ],
    "antibiotic_course": [
        ("Azithromycin 500mg", "antibiotic", 1),
        ("Ciprofloxacin 500mg", "antibiotic", 2),
        ("Amoxicillin 500mg", "antibiotic", 3),
        ("Cefixime 200mg", "antibiotic", 2),
    ],
    "fever_viral": [
        ("Paracetamol 500mg", "other", 3),
    ],
}

_SCHEDULE = {
    "wound_care": [1, 3, 7, 14],
    "antibiotic_course": [1, 3, 7],
    "fever_viral": [1, 3, 5, 7, 14],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _rand_patient_name(rng: random.Random, i: int) -> tuple[str, str, str, str]:
    f = rng.choice(_FIRST_F + _FIRST_M)
    l = rng.choice(_LAST)
    name = f"{f} {l}"
    cg_first = rng.choice(_FIRST_F)  # caregiver — opposite-gender bias
    if f in _FIRST_F:
        cg_first = rng.choice(_FIRST_M)
    return name, f, cg_first, l


def _seed_staff(s):
    pw = settings.ADMIN_PASSWORD or "admin123"
    root_pw = settings.SUPERADMIN_PASSWORD or "root1234"
    users = [
        # Per-hospital admin (the "admin" user)
        User(id=uuid.uuid4().hex, hospital_code=HOSPITAL, username="admin",
             password_hash=hash_password(pw), display_name="District Admin",
             role="admin", ward=None, created_at=_iso(_now())),
        # Cross-hospital superadmin (the "root" user)
        User(id=uuid.uuid4().hex, hospital_code="*", username="root",
             password_hash=hash_password(root_pw), display_name="System Superadmin",
             role="superadmin", ward=None, created_at=_iso(_now())),
        # Demo staff
        User(id=uuid.uuid4().hex, hospital_code=HOSPITAL, username="nurse_kavita",
             password_hash=hash_password("nurse123"), display_name="Nurse Kavita",
             role="nurse", ward="Ward-1", created_at=_iso(_now())),
        User(id=uuid.uuid4().hex, hospital_code=HOSPITAL, username="nurse_lakshmi",
             password_hash=hash_password("nurse123"), display_name="Nurse Lakshmi",
             role="nurse", ward="Ward-2", created_at=_iso(_now())),
        User(id=uuid.uuid4().hex, hospital_code=HOSPITAL, username="dr_suresh",
             password_hash=hash_password("doctor123"), display_name="Dr Suresh",
             role="doctor", ward=None, created_at=_iso(_now())),
    ]
    for u in users:
        s.add(u)
    s.commit()
    return {u.username: u.id for u in users}


def _seed_hospitals(s, with_second: bool = False):
    """Ensure the default hospital (from env) is a row, optionally add a
    second hospital for the multi-hospital demo."""
    default = (s.query(Hospital).filter(Hospital.code == HOSPITAL).first())
    if not default:
        s.add(Hospital(code=HOSPITAL, name=settings.HOSPITAL_NAME, active=1))
    if with_second:
        if not s.query(Hospital).filter(Hospital.code == "KA-DIST-02").first():
            s.add(Hospital(code="KA-DIST-02", name="Tumkur District Hospital",
                           district="Tumkur", state="Karnataka",
                           contact_phone="+919876543210", active=1))
    s.commit()


def _seed_patients(s, user_ids: dict, n: int = 30):
    rng = random.Random(42)
    patients = []
    enrollments = []
    for i in range(n):
        name, _, cg_first, _ = _rand_patient_name(rng, i)
        protocol = rng.choice(list(_CONDITIONS.keys()))
        condition = rng.choice(_CONDITIONS[protocol])
        ward = rng.choice(_WARDS)
        # discharge_date between today-5 and today (so calls are due)
        days_ago = rng.randint(0, 5)
        discharge = (_now() - timedelta(days=days_ago)).date().isoformat()
        sex = "F" if rng.random() < 0.5 else "M"
        age = rng.randint(28, 78)
        phone = f"+919{rng.randint(10**8, 10**9 - 1):09d}"
        cg_phone = f"+919{rng.randint(10**8, 10**9 - 1):09d}"

        p = Patient(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            name=name, age=age, sex=sex,
            caregiver_name=f"{cg_first} {rng.choice(_LAST)}",
            caregiver_phone=phone,
            consent_at=_iso(_now() - timedelta(days=days_ago, hours=2)),
            created_by=user_ids["nurse_kavita"] if i % 2 == 0 else user_ids["nurse_lakshmi"],
            created_at=_iso(_now() - timedelta(days=days_ago)),
        )
        s.add(p)
        s.flush()

        e = Enrollment(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            patient_id=p.id, protocol_id=protocol,
            condition_label=condition, ward=ward,
            discharge_date=discharge,
            status="active",
            number_verified=1 if rng.random() < 0.7 else 0,
            created_by=p.created_by,
            created_at=p.created_at,
        )
        s.add(e)
        s.flush()

        # 1-2 meds
        primary_med = _MEDS[protocol][0]
        extra_meds = []
        if len(_MEDS[protocol]) > 1 and rng.random() < 0.4:
            extra_meds = [_MEDS[protocol][rng.randint(1, len(_MEDS[protocol]) - 1)]]
        for med_name, med_type, doses in [primary_med] + extra_meds:
            s.add(EnrollmentMed(
                id=uuid.uuid4().hex, enrollment_id=e.id,
                med_name=med_name, med_type=med_type, doses_per_day=doses,
            ))

        # schedule followup_calls at discharge + day at 10:00 IST
        for day in _SCHEDULE[protocol]:
            scheduled = (
                datetime.fromisoformat(discharge + "T00:00:00+00:00")
                .astimezone(timezone.utc)
            ) + timedelta(days=day)
            scheduled = scheduled.replace(hour=4, minute=30)  # 10:00 IST
            s.add(FollowupCall(
                id=uuid.uuid4().hex, hospital_code=HOSPITAL,
                enrollment_id=e.id, day_index=day,
                scheduled_at=scheduled.isoformat(),
                status="pending",
            ))

        patients.append(p)
        enrollments.append(e)
    s.commit()
    return patients, enrollments


def _seed_in_progress_calls(s, enrollments):
    """Mark ~8 of the most recent pending calls as in_progress at a question
    node so the live board shows activity."""
    rng = random.Random(7)
    pending = (s.query(FollowupCall)
               .filter(FollowupCall.status == "pending",
                       FollowupCall.hospital_code == HOSPITAL)
               .order_by(FollowupCall.scheduled_at.desc())
               .limit(8).all())
    for c in pending:
        e = next((e for e in enrollments if e.id == c.enrollment_id), None)
        if not e:
            continue
        # mark as in_progress at a question node; assign risk
        c.status = "in_progress"
        c.current_node = rng.choice(["q_wound", "q_meds_today", "q_fever", "q_breath",
                                      "q_symptom_course"])
        c.started_at = _iso(_now() - timedelta(minutes=rng.randint(1, 5)))
        # Sprinkle a risk_reasons JSON
        roll = rng.random()
        if roll < 0.15:
            c.risk_level = "red"
            c.risk_score = 10
            c.risk_reasons = json.dumps(["wound: pus/bleeding/fever (SSI red flag)"])
        elif roll < 0.5:
            c.risk_level = "yellow"
            c.risk_score = 2
            c.risk_reasons = json.dumps(["wound: pain/swelling reported"])
    s.commit()


def _seed_completed_call_with_responses(s, enrollments, protocol: str, risk_level: str, risk_score: int, reasons: list):
    """Insert one completed call with a transcript so the timeline view has data."""
    rng = random.Random(13)
    e = next((e for e in enrollments if e.protocol_id == protocol), None)
    if not e:
        return
    c = FollowupCall(
        id=uuid.uuid4().hex, hospital_code=HOSPITAL,
        enrollment_id=e.id, day_index=1,
        scheduled_at=_iso(_now() - timedelta(days=2, hours=4)),
        started_at=_iso(_now() - timedelta(days=2, hours=4)),
        completed_at=_iso(_now() - timedelta(days=2, hours=3, minutes=50)),
        status="completed",
        risk_level=risk_level, risk_score=risk_score,
        risk_reasons=json.dumps(reasons),
        provider="sim",
        current_node="@end_ok",
    )
    s.add(c); s.flush()
    # A few responses in the transcript
    if protocol == "wound_care":
        for node, digit, score in [("q_wound", "1", 0), ("q_meds_today", "1", 0)]:
            s.add(CallResponse(call_id=c.id, node_id=node, digit=digit, score=score,
                               answered_at=_iso(_now() - timedelta(days=2, hours=3, minutes=55))))
    s.commit()


def _seed_escalations(s, enrollments):
    """1 open RED, 1 acked, 1 resolved-with-recovered-outcome."""
    rng = random.Random(99)

    # 1) open RED on a wound_care patient
    e_red = next((e for e in enrollments if e.protocol_id == "wound_care"), None)
    if e_red:
        c = FollowupCall(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_red.id, day_index=1,
            scheduled_at=_iso(_now() - timedelta(hours=2)),
            started_at=_iso(_now() - timedelta(hours=2)),
            completed_at=_iso(_now() - timedelta(hours=1, minutes=50)),
            status="completed", risk_level="red", risk_score=10,
            risk_reasons=json.dumps(["wound: pus/bleeding/fever (SSI red flag)"]),
            provider="sim", current_node="@end_red",
        )
        s.add(c); s.flush()
        s.add(Escalation(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_red.id, call_id=c.id,
            level="red",
            reasons=json.dumps(["wound: pus/bleeding/fever (SSI red flag)"]),
            status="open",
            created_at=_iso(_now() - timedelta(hours=1, minutes=50)),
        ))

    # 2) acked
    e_ack = next((e for e in enrollments
                  if e.protocol_id == "antibiotic_course" and e.id != (e_red.id if e_red else None)),
                 None)
    if e_ack:
        c = FollowupCall(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_ack.id, day_index=1,
            scheduled_at=_iso(_now() - timedelta(hours=6)),
            started_at=_iso(_now() - timedelta(hours=6)),
            completed_at=_iso(_now() - timedelta(hours=5, minutes=50)),
            status="completed", risk_level="red", risk_score=10,
            risk_reasons=json.dumps(["fever: high fever with rigor"]),
            provider="sim", current_node="@end_red",
        )
        s.add(c); s.flush()
        admin = s.query(User).filter(User.username == "admin").first()
        s.add(Escalation(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_ack.id, call_id=c.id, level="red",
            reasons=json.dumps(["fever: high fever with rigor"]),
            status="acked",
            acked_by=admin.id, acked_at=_iso(_now() - timedelta(hours=5)),
            created_at=_iso(_now() - timedelta(hours=5, minutes=50)),
        ))

    # 3) resolved with outcome
    e_res = next((e for e in enrollments
                  if e.protocol_id == "wound_care" and e.id not in
                  {e_red.id if e_red else None, e_ack.id if e_ack else None}),
                 None)
    if e_res:
        c = FollowupCall(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_res.id, day_index=1,
            scheduled_at=_iso(_now() - timedelta(days=3, hours=4)),
            started_at=_iso(_now() - timedelta(days=3, hours=4)),
            completed_at=_iso(_now() - timedelta(days=3, hours=3, minutes=50)),
            status="completed", risk_level="yellow", risk_score=2,
            risk_reasons=json.dumps(["wound: pain/swelling reported"]),
            provider="sim", current_node="@end_ok",
        )
        s.add(c); s.flush()
        admin = s.query(User).filter(User.username == "admin").first()
        s.add(Escalation(
            id=uuid.uuid4().hex, hospital_code=HOSPITAL,
            enrollment_id=e_res.id, call_id=c.id, level="red",
            reasons=json.dumps(["wound: pain/swelling reported"]),
            status="resolved",
            acked_by=admin.id, acked_at=_iso(_now() - timedelta(days=3, hours=2)),
            resolved_by=admin.id, resolved_at=_iso(_now() - timedelta(days=3, hours=1)),
            resolution_note="called family, adjusted dressing, stable",
            created_at=_iso(_now() - timedelta(days=3, hours=3, minutes=50)),
        ))
        e_res.outcome = "recovered"

    s.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="Wipe all tables then re-seed the default staff")
    ap.add_argument("--wipe", action="store_true",
                    help="Wipe every row from every table; do NOT seed anything")
    ap.add_argument("--with-demo-data", action="store_true",
                    help="Populate 30 patients, live calls, escalations")
    ap.add_argument("--with-second-hospital", action="store_true",
                    help="Also add KA-DIST-02 (Tumkur) for the multi-hospital demo")
    args = ap.parse_args()

    init_engine(settings.DATABASE_URL)
    s = SessionLocal()

    if args.reset or args.wipe:
        # Wipe in FK-safe order. `pending_notifications` and `telegram_sessions`
        # are wiped too so the demo board always starts clean.
        for m in [CallResponse, Escalation, FollowupCall, EnrollmentMed,
                  Enrollment, Patient, User, Hospital, PendingNotification,
                  TelegramSession]:
            try:
                s.query(m).delete()
                s.commit()
            except Exception:
                s.rollback()

    if args.wipe:
        n_total = sum(s.query(m).count() for m in
                      [CallResponse, Escalation, FollowupCall, EnrollmentMed,
                       Enrollment, Patient, User, Hospital,
                       PendingNotification, TelegramSession])
        print(f"wiped all tables · rows remaining: {n_total}")
        s.close()
        return

    if s.query(User).count() == 0:
        _seed_hospitals(s, with_second=args.with_second_hospital)
        user_ids = _seed_staff(s)
        print(f"seed complete · staff: {', '.join(user_ids.keys())}")
    else:
        user_ids = {u.username: u.id for u in s.query(User).all()}
        # When --with-demo-data is set, ensure the 3 demo staff exist (idempotent
        # — re-seeding won't duplicate because of username UNIQUE).
        if args.with_demo_data and not all(u in user_ids for u in
                                          ("nurse_kavita", "nurse_lakshmi", "dr_suresh")):
            for username, password, display, role, ward in [
                ("nurse_kavita", "nurse123", "Nurse Kavita", "nurse", "Ward-1"),
                ("nurse_lakshmi", "nurse123", "Nurse Lakshmi", "nurse", "Ward-2"),
                ("dr_suresh", "doctor123", "Dr Suresh", "doctor", None),
            ]:
                if username not in user_ids:
                    s.add(User(
                        id=uuid.uuid4().hex, hospital_code=HOSPITAL,
                        username=username,
                        password_hash=hash_password(password),
                        display_name=display, role=role, ward=ward,
                        created_at=_iso(_now()),
                    ))
            s.commit()
            user_ids = {u.username: u.id for u in s.query(User).all()}
        # Always ensure hospitals exist (idempotent)
        _seed_hospitals(s, with_second=args.with_second_hospital)

    if args.with_demo_data:
        patients, enrollments = _seed_patients(s, user_ids, n=30)
        _seed_in_progress_calls(s, enrollments)
        _seed_completed_call_with_responses(
            s, enrollments, "wound_care", "green", 0, ["wound: healing well"],
        )
        _seed_completed_call_with_responses(
            s, enrollments, "antibiotic_course", "yellow", 2,
            ["doses missed today"],
        )
        _seed_escalations(s, enrollments)

        # Print the demo-ready summary
        from app.models import FollowupCall as FC, Escalation as Esc
        n_enrolled = s.query(Enrollment).filter(Enrollment.hospital_code == HOSPITAL).count()
        n_pending = s.query(FC).filter(FC.hospital_code == HOSPITAL, FC.status == "pending").count()
        n_inprog = s.query(FC).filter(FC.hospital_code == HOSPITAL, FC.status == "in_progress").count()
        n_completed = s.query(FC).filter(FC.hospital_code == HOSPITAL, FC.status == "completed").count()
        n_open_esc = s.query(Esc).filter(Esc.hospital_code == HOSPITAL, Esc.status == "open").count()
        n_acked_esc = s.query(Esc).filter(Esc.hospital_code == HOSPITAL, Esc.status == "acked").count()
        n_resolved_esc = s.query(Esc).filter(Esc.hospital_code == HOSPITAL, Esc.status == "resolved").count()
        n_recovered = s.query(Enrollment).filter(Enrollment.hospital_code == HOSPITAL, Enrollment.outcome == "recovered").count()
        n_hospitals = s.query(Hospital).count()
        n_super = s.query(User).filter(User.role == "superadmin").count()

        print("")
        print("demo board ready:")
        print(f"  hospitals seeded:       {n_hospitals}")
        print(f"  superadmin users:       {n_super}")
        print(f"  patients enrolled:      {n_enrolled}")
        print(f"  pending followup calls: {n_pending}")
        print(f"  in-progress calls:      {n_inprog}  (live SSE on /board)")
        print(f"  completed calls:        {n_completed}")
        print(f"  open RED escalations:   {n_open_esc}  (SOS banner visible)")
        print(f"  acked escalations:      {n_acked_esc}")
        print(f"  resolved escalations:   {n_resolved_esc}")
        print(f"  outcomes=recovered:     {n_recovered}")
        print("")
        pw = settings.ADMIN_PASSWORD or "admin123"
        root_pw = settings.SUPERADMIN_PASSWORD or "root1234"
        print(f"log in as admin / {pw}        (per-hospital admin)")
        print(f"log in as root  / {root_pw}   (cross-hospital superadmin)")

    s.close()


def seed_health_data():
    """Seed demo health data for existing patients (for demo purposes)."""
    import random
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

        for day_offset in range(7, 0, -1):
            day = now - timedelta(days=day_offset)
            base_hr = 85 - day_offset
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
            base_spo2 = 94 + (day_offset // 3)
            spo2 = min(99, base_spo2 + random.randint(-1, 1))
            s.add(PatientHealthData(
                id=uuid.uuid4().hex,
                patient_id=patient.id,
                hospital_code=HOSPITAL,
                metric_type="spo2",
                value=float(spo2),
                unit="%",
                recorded_at=day.replace(hour=10, minute=0).isoformat(),
                source="demo_seed",
            ))
            stored += 2

    s.commit()
    print(f"seeded {stored} health data points for {min(3, len(patients))} patients")
    s.close()


if __name__ == "__main__":
    main()
