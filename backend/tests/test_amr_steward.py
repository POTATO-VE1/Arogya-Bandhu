"""Tests for AMR stewardship module."""
import time

from app.db import now_utc
from app.models import Enrollment, EnrollmentMed, FollowupCall, Patient, User

ADMIN = {"username": "admin", "password": "changeme123"}


def _login(client):
    client.post("/api/auth/login", json=ADMIN)


def _seed_enrollment(db, with_abx=True, verified=True) -> tuple[str, str]:
    """Create a user + patient + enrollment. Returns (patient_id, enrollment_id)."""
    u = User(hospital_code="KA-DIST-01", username="testuser", password_hash="x",
             display_name="Test", role="staff", created_at=now_utc())
    db.add(u); db.commit(); db.refresh(u)
    p = Patient(hospital_code="KA-DIST-01", name="Test Patient", age=45,
                caregiver_name="Caregiver", caregiver_phone="+919876543210",
                consent_at=now_utc(), created_by=u.id)
    db.add(p); db.commit(); db.refresh(p)
    e = Enrollment(hospital_code="KA-DIST-01", patient_id=p.id, protocol_id="wound_care",
                   condition_label="Post-op", discharge_date="2026-07-25",
                   number_verified=1 if verified else 0)
    db.add(e); db.commit(); db.refresh(e)
    if with_abx:
        db.add(EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav", med_type="antibiotic",
                             aware_category="Watch", course_days=5, doses_per_day=2))
        db.commit()
    return p.id, e.id


# ── confirm_meds ───────────────────────────────────────────────────────────────

def test_confirm_meds_returns_success(db):
    from app.amr_steward import confirm_meds
    _seed_enrollment(db)
    result = confirm_meds("+919876543210")
    assert "ಧನ್ಯವಾದಗಳು" in result or "confirm" in result.lower() or "✓" in result


def test_confirm_meds_unknown_phone(db):
    from app.amr_steward import confirm_meds
    result = confirm_meds("+910000000000")
    assert "No active enrollment" in result


# ── report_pill_count ──────────────────────────────────────────────────────────

def test_report_pill_count_zero(db):
    from app.amr_steward import report_pill_count
    _seed_enrollment(db)
    result = report_pill_count("+919876543210", 0)
    assert "ಎಲ್ಲಾ" in result or "0/" in result


def test_report_pill_count_high(db):
    from app.amr_steward import report_pill_count
    _seed_enrollment(db)
    result = report_pill_count("+919876543210", 8)
    assert "⚠️" in result or "ಮಾತ್ರೆ" in result


def test_report_pill_count_mid(db):
    from app.amr_steward import report_pill_count
    _seed_enrollment(db)
    result = report_pill_count("+919876543210", 4)
    assert "📊" in result or "ಮಾತ್ರೆ" in result


def test_report_pill_count_no_abx(db):
    from app.amr_steward import report_pill_count
    _seed_enrollment(db, with_abx=False)
    result = report_pill_count("+919876543210", 5)
    assert "No antibiotic" in result


# ── trigger endpoint ───────────────────────────────────────────────────────────

def test_trigger_steward_endpoint(client, db):
    _login(client)
    _seed_enrollment(db)
    r = client.post("/api/amr/steward/trigger")
    assert r.status_code == 200
    data = r.json()
    assert "reminders_sent" in data
    assert "pill_checks_sent" in data
    assert "non_adherence_escalations" in data


def test_steward_status_endpoint(client, db):
    _login(client)
    r = client.get("/api/amr/steward/status")
    assert r.status_code == 200
    data = r.json()
    assert "reminders_today" in data
    assert "pill_responses" in data


def test_trigger_steward_requires_auth(client):
    r = client.post("/api/amr/steward/trigger")
    assert r.status_code == 401


# ── daily reminders ────────────────────────────────────────────────────────────

def test_send_daily_reminders_no_telegram(db):
    """Without TELEGRAM_BOT_TOKEN, reminders return 0 sent (no crash)."""
    from app.amr_steward import send_daily_reminders
    _seed_enrollment(db)
    sent = send_daily_reminders()
    # TELEGRAM_BOT_TOKEN is empty in tests, so 0 sent
    assert sent == 0


# ── non-adherence escalation ──────────────────────────────────────────────────

def test_check_non_adherence_creates_escalation(db):
    from app.amr_steward import check_non_adherence, _reminded_today
    from app.models import Escalation
    _, eid = _seed_enrollment(db)
    # simulate reminder sent 25h ago
    _reminded_today[eid] = time.time() - 90000
    escalated = check_non_adherence()
    assert escalated >= 1
    esc = db.query(Escalation).filter(
        Escalation.enrollment_id == eid,
        Escalation.status == "open",
    ).first()
    assert esc is not None
    assert "AMR steward" in esc.reasons


def test_check_non_adherence_skips_confirmed(db):
    from app.amr_steward import check_non_adherence, _reminded_today, _meds_confirmed
    _, eid = _seed_enrollment(db)
    _reminded_today[eid] = time.time() - 90000
    _meds_confirmed[eid] = time.time()  # confirmed after reminder
    escalated = check_non_adherence()
    assert escalated == 0
