"""T9 acceptance: enrollment creates calls, consent guard, board, webhooks render TwiML."""
import json

from app.db import now_utc
from app.models import Enrollment, EnrollmentMed, FollowupCall, Patient, User

ADMIN = {"username": "admin", "password": "changeme123"}


def _login(client):
    client.post("/api/auth/login", json=ADMIN)


def _enroll_body(phone="+919876543210", consent=True, protocol="wound_care"):
    return {
        "patient": {"name": "Lakshmamma", "age": 58, "sex": "F", "abha_number": None,
                    "caregiver_name": "Ramu", "caregiver_phone": phone},
        "protocol_id": protocol, "condition_label": "Post-op appendectomy",
        "ward": "Ward-4", "discharge_date": "2026-07-25",
        "meds": [{"med_name": "Amoxiclav 625mg", "med_type": "antibiotic",
                  "aware_category": "Watch", "course_days": 5, "doses_per_day": 2},
                 {"med_name": "Paracetamol 500mg", "med_type": "other"}],
        "consent": consent,
    }


def test_enroll_creates_four_scheduled_calls(client, db):
    _login(client)
    r = client.post("/api/enrollments", json=_enroll_body())
    assert r.status_code == 201, r.text
    eid = r.json()["enrollment_id"]
    calls = (db.query(FollowupCall).filter(FollowupCall.enrollment_id == eid)
             .order_by(FollowupCall.day_index).all())
    assert [c.day_index for c in calls] == [1, 3, 7, 14]
    # discharge 2026-07-25 + 1 day at 10:00 IST = 04:30 UTC
    assert calls[0].scheduled_at.startswith("2026-07-26T04:30:00")
    meds = db.query(EnrollmentMed).filter(EnrollmentMed.enrollment_id == eid).all()
    assert len(meds) == 2
    assert meds[0].aware_category == "Watch"


def test_consent_required(client, db):
    _login(client)
    r = client.post("/api/enrollments", json=_enroll_body(consent=False))
    assert r.status_code == 422


def test_bad_phone_rejected(client, db):
    _login(client)
    b = _enroll_body(phone="98765")
    r = client.post("/api/enrollments", json=b)
    assert r.status_code == 422


def test_unknown_protocol_rejected(client, db):
    _login(client)
    b = _enroll_body()
    b["protocol_id"] = "nope"
    r = client.post("/api/enrollments", json=b)
    assert r.status_code == 422


def test_board_requires_session(client):
    assert client.get("/api/board").status_code == 401


def test_board_lists_enrollment(client, db):
    _login(client)
    client.post("/api/enrollments", json=_enroll_body())
    r = client.get("/api/board")
    assert r.status_code == 200
    assert r.json()["kpis"]["enrolled"] == 1
    assert len(r.json()["rows"]) == 1
    assert r.json()["rows"][0]["patient_name"] == "Lakshmamma"


def test_trigger_sim_creates_call(client, db):
    _login(client)
    eid = client.post("/api/enrollments", json=_enroll_body()).json()["enrollment_id"]
    r = client.post("/api/demo/trigger-call", json={
        "enrollment_id": eid, "channel": "sim"})
    assert r.status_code == 200, r.text
    cid = r.json()["call_id"]
    call = db.get(FollowupCall, cid)
    assert call.provider == "sim" and call.status == "pending"


def test_trigger_twilio_unconfigured_503(client, db):
    _login(client)
    eid = client.post("/api/enrollments", json=_enroll_body()).json()["enrollment_id"]
    r = client.post("/api/demo/trigger-call", json={
        "enrollment_id": eid, "channel": "twilio"})
    assert r.status_code == 503  # twilio not configured in test env


# ── webhooks (signature off in tests) ─────────────────────────────────────────
def _seed_call_row(db, protocol="wound_care", with_abx=True) -> str:
    u = User(hospital_code="KA-DIST-01", username="u", password_hash="x",
             display_name="U")
    db.add(u); db.commit(); db.refresh(u)
    p = Patient(hospital_code="KA-DIST-01", name="X", age=60,
                caregiver_name="R", caregiver_phone="+919876543210",
                consent_at=now_utc(), created_by=u.id)
    db.add(p); db.commit(); db.refresh(p)
    e = Enrollment(hospital_code="KA-DIST-01", patient_id=p.id, protocol_id=protocol,
                   condition_label="Post-op", discharge_date="2026-07-25")
    db.add(e); db.commit(); db.refresh(e)
    if with_abx:
        db.add(EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav",
                             med_type="antibiotic", aware_category="Watch",
                             course_days=5, doses_per_day=2))
        db.commit()
    c = FollowupCall(hospital_code="KA-DIST-01", enrollment_id=e.id,
                     day_index=1, scheduled_at=now_utc())
    db.add(c); db.commit(); db.refresh(c)
    return c.id


def test_webhook_voice_renders_gather_with_greet(client, db):
    cid = _seed_call_row(db)
    r = client.post(f"/webhooks/twilio/voice/{cid}")
    assert r.status_code == 200
    body = r.text
    assert "<Gather" in body
    assert "/audio/greet.mp3" in body
    assert "/audio/confirm_family.mp3" in body


def test_webhook_gather_digits_drives_engine(client, db):
    cid = _seed_call_row(db)
    client.post(f"/webhooks/twilio/voice/{cid}")
    r = client.post(f"/webhooks/twilio/gather/{cid}", data={"Digits": "1"})
    assert r.status_code == 200
    # after confirm_family → q_wound: gather should now reference q_wound prompt
    assert "/audio/q_wound.mp3" in r.text


def test_webhook_gather_red_completes_and_escalates(client, db):
    cid = _seed_call_row(db)
    client.post(f"/webhooks/twilio/voice/{cid}")
    client.post(f"/webhooks/twilio/gather/{cid}", data={"Digits": "1"})  # family
    r = client.post(f"/webhooks/twilio/gather/{cid}", data={"Digits": "3"})  # wound red
    assert r.status_code == 200
    call = db.get(FollowupCall, cid)
    assert call.status == "completed" and call.risk_level == "red"
    from app.models import Escalation
    assert db.query(Escalation).filter(Escalation.enrollment_id == call.enrollment_id).count() == 1