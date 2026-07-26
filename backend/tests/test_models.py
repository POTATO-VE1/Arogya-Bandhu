"""T2 acceptance: insert+read one row per table; schema matches docs/02 §5."""
import sqlite3, os, pathlib
from sqlalchemy import inspect

from app.models import (
    User, Patient, Enrollment, EnrollmentMed,
    FollowupCall, CallResponse, Escalation, AuditLog,
)


def test_all_tables_present(db):
    names = set(inspect(db.bind).get_table_names())
    expected = {
        "users", "patients", "enrollments", "enrollment_meds",
        "followup_calls", "call_responses", "escalations", "audit_log",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_roundtrip_one_row_per_table(db):
    u = User(hospital_code="KA-DIST-01", username="nurse01",
             password_hash="x", display_name="Nurse One")
    db.add(u); db.commit(); db.refresh(u)

    p = Patient(hospital_code="KA-DIST-01", name="Lakshmamma", age=58, sex="F",
                caregiver_name="Ramu", caregiver_phone="+919876543210",
                consent_at="2026-07-25T03:00:00+00:00",
                created_by=u.id)
    db.add(p); db.commit(); db.refresh(p)

    e = Enrollment(hospital_code="KA-DIST-01", patient_id=p.id,
                   protocol_id="wound_care", condition_label="Post-op",
                   discharge_date="2026-07-25")
    db.add(e); db.commit(); db.refresh(e)

    m = EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav 625mg",
                      med_type="antibiotic", aware_category="Watch",
                      course_days=5, doses_per_day=2)
    db.add(m); db.commit()

    c = FollowupCall(hospital_code="KA-DIST-01", enrollment_id=e.id,
                     day_index=1, scheduled_at="2026-07-26T04:30:00+00:00")
    db.add(c); db.commit(); db.refresh(c)

    r = CallResponse(call_id=c.id, node_id="q_wound", digit="1", score=0)
    db.add(r); db.commit()

    esc = Escalation(hospital_code="KA-DIST-01", enrollment_id=e.id,
                     call_id=c.id, reasons='["wound: pus"]')
    db.add(esc); db.commit()

    au = AuditLog(hospital_code="KA-DIST-01", actor="nurse01", action="enroll",
                  entity_id=e.id)
    db.add(au); db.commit()

    assert db.query(User).count() == 1
    assert db.query(Patient).count() == 1
    assert db.query(Enrollment).count() == 1
    assert db.query(EnrollmentMed).count() == 1
    assert db.query(FollowupCall).count() == 1
    assert db.query(CallResponse).count() == 1
    assert db.query(Escalation).count() == 1
    assert db.query(AuditLog).count() == 1
    assert db.query(EnrollmentMed).first().aware_category == "Watch"


def test_schema_matches_docs(tmp_path):
    init_url = f"sqlite:///{tmp_path}/schema.db"
    import os
    os.environ["DATABASE_URL"] = init_url
    from app.db import init_engine, SessionLocal
    init_engine(init_url)
    SessionLocal().close()
    conn = sqlite3.connect(str(tmp_path / "schema.db"))
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name")
    text = "\n".join(r[0] for r in ddl if r[0])
    conn.close()
    # spot-check key columns that must exist exactly as spec'd (docs/02 §5)
    for col in [
        "number_verified", "risk_reasons", "provider_call_sid",
        "aware_category", "course_days", "consent_at", "acked_by", "doses_per_day",
    ]:
        assert col in text, f"column {col} missing from schema"