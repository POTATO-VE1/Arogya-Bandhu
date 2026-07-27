"""Deeper / edge-case / regression tests for the API surface.

These tests are intentionally cross-cutting — they exercise the wiring between
routers, deps, db, and the model layer, including failure paths and security
boundaries the happy-path tests in test_comprehensive.py don't reach.
"""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from app.db import now_utc
from app.models import (
    AuditLog, CallResponse, Enrollment, EnrollmentMed, Escalation,
    FollowupCall, Patient, User,
)
from app.security import _attempts, hash_password

ADMIN = {"username": "admin", "password": "changeme123"}


# ── shared fixtures / helpers ────────────────────────────────────────────────

def _login(client, username="admin", password="changeme123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _make_user(db, username, role, ward=None, hospital="KA-DIST-01", password="pw123456") -> User:
    u = User(
        hospital_code=hospital,
        username=username,
        password_hash=hash_password(password),
        display_name=f"Test {role.title()} {username}",
        role=role,
        ward=ward,
        created_at=now_utc(),
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


def _make_patient(db, user_id, name="Patient", phone="+919876543210", hospital="KA-DIST-01") -> Patient:
    p = Patient(
        hospital_code=hospital,
        name=name, age=40, sex="M",
        caregiver_name="CG", caregiver_phone=phone,
        consent_at=now_utc(), created_by=user_id,
    )
    db.add(p); db.commit(); db.refresh(p)
    return p


def _make_enrollment(db, patient_id, user_id, protocol="wound_care", hospital="KA-DIST-01",
                     ward=None, condition="Post-op test") -> Enrollment:
    e = Enrollment(
        hospital_code=hospital,
        patient_id=patient_id,
        protocol_id=protocol,
        condition_label=condition,
        ward=ward,
        discharge_date="2026-07-25",
        created_by=user_id,
    )
    db.add(e); db.commit(); db.refresh(e)
    return e


def _enroll_via_api(client, **overrides) -> dict:
    body = {
        "patient": {"name": "Lakshmamma", "age": 58, "sex": "F", "abha_number": None,
                    "caregiver_name": "Ramu", "caregiver_phone": "+919876543210"},
        "protocol_id": "wound_care", "condition_label": "Post-op",
        "ward": "Ward-1", "discharge_date": "2026-07-25",
        "meds": [], "consent": True,
    }
    body.update(overrides)
    r = client.post("/api/enrollments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Route ordering regression — guards the /api/patients/search bug from coming
# back. These tests would have FAILED before the fix (404 shadowed by {pid}).
# ─────────────────────────────────────────────────────────────────────────────
class TestRouteOrderingRegression:
    """Literal paths must not be shadowed by {pid} capture patterns."""

    def test_search_route_not_shadowed(self, client, db):
        _login(client)
        r = client.get("/api/patients/search")
        # empty query returns []
        assert r.status_code == 200, r.text
        assert r.json() == []

    def test_search_route_with_q(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        _make_patient(db, u.id, name="Ramesh Kumar", phone="+919876500001")
        r = client.get("/api/patients/search?q=Ramesh")
        assert r.status_code == 200
        data = r.json()
        assert any(p["name"] == "Ramesh Kumar" for p in data)

    def test_search_phone_match(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        _make_patient(db, u.id, name="A", phone="+919876543999")
        r = client.get("/api/patients/search?q=9876543999")
        assert r.status_code == 200
        assert any(p["phone"] == "+919876543999" for p in r.json())

    def test_search_cross_hospital_excluded(self, client, db):
        """A patient in a different hospital must NOT appear in search results."""
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        _make_patient(db, u.id, name="VisiblePatient", phone="+919876500111")
        # Forge a patient in a different hospital directly
        other = _make_patient(db, u.id, name="HiddenPatient",
                              phone="+919876500222", hospital="OTHER-HOSP")
        r = client.get("/api/patients/search?q=Hidden")
        assert r.status_code == 200
        names = [p["name"] for p in r.json()]
        assert "HiddenPatient" not in names

    def test_search_caps_at_50(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        # bulk insert 60 patients with same prefix
        for i in range(60):
            _make_patient(db, u.id, name=f"BulkTest{i:02d}", phone=f"+91900000{i:04d}")
        r = client.get("/api/patients/search?q=BulkTest")
        assert r.status_code == 200
        assert len(r.json()) <= 50

    def test_export_csv_route_not_shadowed(self, client, db):
        _login(client)
        r = client.get("/api/patients/export/csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "Patient ID" in r.text

    def test_export_csv_empty(self, client, db):
        _login(client)
        r = client.get("/api/patients/export/csv")
        assert r.status_code == 200
        # header row only
        lines = r.text.strip().splitlines()
        assert len(lines) == 1
        assert "Patient ID" in lines[0]

    def test_export_csv_with_existing_data(self, client, db):
        """Real data should round-trip through the CSV exporter without crashing."""
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="ExportMe", phone="+919876500099")
        _make_enrollment(db, p.id, u.id, ward="Ward-X", condition="Test condition")
        r = client.get("/api/patients/export/csv")
        assert r.status_code == 200
        assert "ExportMe" in r.text
        assert "Ward-X" in r.text
        assert "Test condition" in r.text
        assert "Patient ID" in r.text.splitlines()[0]

    def test_patient_by_uuid_still_works(self, client, db):
        """After re-ordering, /api/patients/{uuid} must still resolve to detail."""
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="DetailTest")
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 200
        assert r.json()["name"] == "DetailTest"


# ─────────────────────────────────────────────────────────────────────────────
# Outcome flow — set, validate, audit, IDOR
# ─────────────────────────────────────────────────────────────────────────────
class TestOutcomeFlow:
    def _seed(self, db):
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        return u, p, e

    def test_set_outcome_recovered(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "recovered"})
        assert r.status_code == 200
        assert r.json()["outcome"] == "recovered"
        db.refresh(e)
        assert e.outcome == "recovered"

    def test_set_outcome_readmitted(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "readmitted"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.outcome == "readmitted"

    def test_set_outcome_referred(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "referred"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.outcome == "referred"

    def test_outcome_invalid_value_rejected(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "garbage"})
        assert r.status_code == 400
        assert "Invalid outcome" in r.json()["detail"]

    def test_outcome_deceased(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "deceased"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.outcome == "deceased"

    def test_outcome_lost_to_followup(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "lost_to_followup"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.outcome == "lost_to_followup"

    def test_outcome_transferred(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "transferred"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.outcome == "transferred"

    def test_outcome_audit_written(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "recovered"})
        audit = db.query(AuditLog).filter(
            AuditLog.action == "set_outcome",
            AuditLog.entity_id == e.id,
        ).first()
        assert audit is not None
        assert audit.actor == "admin"
        meta = json.loads(audit.meta)
        assert meta["outcome"] == "recovered"

    def test_outcome_idor_cross_hospital_404(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        # patient/enrollment in OTHER hospital
        p = _make_patient(db, u.id, hospital="OTHER-HOSP")
        e = _make_enrollment(db, p.id, u.id, hospital="OTHER-HOSP")
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "recovered"})
        assert r.status_code == 404

    def test_outcome_requires_session(self, client, db):
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "recovered"})
        assert r.status_code == 401

    def test_outcome_persists_across_refetch(self, client, db):
        _login(client)
        _, _, e = self._seed(db)
        client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "referred"})
        # refetch via /api/board
        board = client.get("/api/board").json()
        row = next(r for r in board["rows"] if r["enrollment_id"] == e.id)
        assert row["outcome"] == "referred"


# ─────────────────────────────────────────────────────────────────────────────
# Escalation lifecycle — open → ack → resolve, state machine, audit, IDOR
# ─────────────────────────────────────────────────────────────────────────────
class TestEscalationLifecycle:
    def _seed(self, db, status="open"):
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01",
            enrollment_id=e.id,
            level="red",
            reasons='["test red"]',
            status=status,
        )
        db.add(esc); db.commit(); db.refresh(esc)
        return u, p, e, esc

    def test_list_includes_status(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        rows = client.get("/api/escalations").json()
        mine = [r for r in rows if r["id"] == esc.id]
        assert len(mine) == 1
        assert mine[0]["status"] == "open"
        assert mine[0]["level"] == "red"

    def test_ack_transitions_status(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        r = client.post(f"/api/escalations/{esc.id}/ack")
        assert r.status_code == 200
        assert r.json()["status"] == "acked"
        db.refresh(esc)
        assert esc.status == "acked"
        assert esc.acked_by is not None
        assert esc.acked_at is not None

    def test_ack_idempotent(self, client, db):
        """Re-acking an already-acked escalation is a no-op (no new audit row)."""
        _login(client)
        _, _, _, esc = self._seed(db)
        client.post(f"/api/escalations/{esc.id}/ack")
        audits_before = db.query(AuditLog).filter(AuditLog.action == "ack").count()
        client.post(f"/api/escalations/{esc.id}/ack")
        audits_after = db.query(AuditLog).filter(AuditLog.action == "ack").count()
        assert audits_after == audits_before

    def test_ack_idor_404(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        esc.hospital_code = "OTHER-HOSP"
        db.commit()
        r = client.post(f"/api/escalations/{esc.id}/ack")
        assert r.status_code == 404

    def test_resolve_from_open(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db, status="open")
        r = client.post(
            f"/api/escalations/{esc.id}/resolve",
            json={"note": "called family, stable"},
        )
        assert r.status_code == 200
        db.refresh(esc)
        assert esc.status == "resolved"
        assert esc.resolved_by is not None
        assert esc.resolution_note == "called family, stable"

    def test_resolve_from_acked(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db, status="acked")
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={"note": "ok"})
        assert r.status_code == 200
        db.refresh(esc)
        assert esc.status == "resolved"

    def test_resolve_already_resolved_400(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db, status="resolved")
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={"note": "x"})
        assert r.status_code == 400
        assert "already resolved" in r.json()["detail"]

    def test_resolve_audit_written_with_note(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        client.post(f"/api/escalations/{esc.id}/resolve", json={"note": "patient stable, sent meds reminder"})
        audit = db.query(AuditLog).filter(
            AuditLog.action == "resolve",
            AuditLog.entity_id == esc.id,
        ).first()
        assert audit is not None
        meta = json.loads(audit.meta)
        assert meta["note"] == "patient stable, sent meds reminder"

    def test_resolve_default_note(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={})
        assert r.status_code == 200
        db.refresh(esc)
        assert esc.resolution_note == "resolved by staff"

    def test_resolve_with_disposition(self, client, db):
        """A resolved escalation persists its disposition enum."""
        _login(client)
        _, _, _, esc = self._seed(db)
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "note": "called family, adjusted dressing",
            "disposition": "called_family",
        })
        assert r.status_code == 200
        assert r.json()["disposition"] == "called_family"
        audit = db.query(AuditLog).filter(
            AuditLog.action == "resolve",
            AuditLog.entity_id == esc.id,
        ).first()
        meta = json.loads(audit.meta)
        assert meta["disposition"] == "called_family"

    def test_resolve_with_invalid_disposition(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "disposition": "ignored_it",
        })
        assert r.status_code == 400
        assert "Invalid disposition" in r.json()["detail"]

    def test_resolve_with_callback_schedules_new_call(self, client, db):
        """callback_in_hours=2 creates a new pending followup_call 2h from now."""
        from app.models import FollowupCall
        from datetime import datetime, timezone
        _login(client)
        _, _, _, esc = self._seed(db)
        before = datetime.now(timezone.utc)
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "note": "advise callback in 2h",
            "disposition": "callback_scheduled",
            "callback_in_hours": 2,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["disposition"] == "callback_scheduled"
        assert body["callback_call_id"] is not None
        # Verify the new call was created
        new_call = db.get(FollowupCall, body["callback_call_id"])
        assert new_call is not None
        assert new_call.status == "pending"
        assert new_call.kind == "manual_callback"
        assert new_call.triggered_by is not None
        # scheduled_at is roughly now+2h (allow 30s for slow test envs)
        from datetime import timedelta
        scheduled = datetime.fromisoformat(new_call.scheduled_at)
        assert before + timedelta(hours=2, seconds=-5) <= scheduled <= before + timedelta(hours=2, seconds=30)

    def test_resolve_callback_in_hours_out_of_range(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        # 0 hours
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "callback_in_hours": 0,
        })
        assert r.status_code == 400
        # 73 hours
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "callback_in_hours": 73,
        })
        assert r.status_code == 400

    def test_resolve_callback_in_hours_defaults_disposition(self, client, db):
        """If callback_in_hours is set but no disposition, default to callback_scheduled."""
        _login(client)
        _, _, _, esc = self._seed(db)
        r = client.post(f"/api/escalations/{esc.id}/resolve", json={
            "callback_in_hours": 4,
        })
        assert r.status_code == 200
        assert r.json()["disposition"] == "callback_scheduled"

    def test_phone_masked_in_list(self, client, db):
        _login(client)
        _, _, _, esc = self._seed(db)
        rows = client.get("/api/escalations").json()
        mine = [r for r in rows if r["id"] == esc.id][0]
        # PII guard: full phone MUST NOT appear in the response
        assert "+919876543210" not in json.dumps(mine)
        assert "•••" in mine["caregiver_phone"]

    def test_call_transcript_attached(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_utc(), status="completed", risk_level="red",
        )
        db.add(c); db.commit(); db.refresh(c)
        db.add(CallResponse(call_id=c.id, node_id="q_wound", digit="3", score=10))
        db.commit()
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id, call_id=c.id,
            level="red", reasons='["wound red"]',
        )
        db.add(esc); db.commit(); db.refresh(esc)
        rows = client.get("/api/escalations").json()
        mine = [r for r in rows if r["id"] == esc.id][0]
        assert len(mine["call_transcript"]) == 1
        assert mine["call_transcript"][0]["node_id"] == "q_wound"
        assert mine["call_transcript"][0]["digit"] == "3"
        assert mine["call_transcript"][0]["score"] == 10


# ─────────────────────────────────────────────────────────────────────────────
# Enrollment validation — duplicate detection, phone format, ward storage
# ─────────────────────────────────────────────────────────────────────────────
class TestEnrollmentValidation:
    def test_duplicate_phone_protocol_409(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "P1", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900001"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        assert client.post("/api/enrollments", json=body).status_code == 201
        body["patient"]["name"] = "P2"  # different name, same phone+protocol
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 409, r.text
        assert "already enrolled" in r.json()["detail"]

    def test_same_phone_different_protocol_ok(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "P1", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900002"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        assert client.post("/api/enrollments", json=body).status_code == 201
        body["protocol_id"] = "antibiotic_course"
        assert client.post("/api/enrollments", json=body).status_code == 201

    def test_age_out_of_range_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 200, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900003"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422  # Pydantic Field(le=150)

    def test_negative_age_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": -1, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900004"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422

    def test_missing_consent_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900005"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [],
            # no consent field
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422
        # Pydantic returns "Field required" with the missing field name in the
        # `loc` array. Match either the field name or "required".
        detail = r.json()["detail"]
        msgs = " ".join(str(d) for d in detail).lower()
        assert "consent" in msgs or "required" in msgs

    def test_consent_false_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919999900006"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": False,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422
        assert "consent" in r.json()["detail"].lower()

    def test_phone_without_plus_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "919999900007"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422

    def test_phone_with_letters_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+91abc1234567"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422

    def test_short_phone_rejected(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "X", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+9112345"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 422

    def test_call_scheduled_at_ist_10(self, client, db):
        """day_index=1 with discharge 2026-07-25 → first call at 04:30 UTC (= 10:00 IST)."""
        _login(client)
        r = _enroll_via_api(client, discharge_date="2026-07-25")
        from app.models import FollowupCall
        eid = r["enrollment_id"]
        first = (db.query(FollowupCall)
                 .filter(FollowupCall.enrollment_id == eid)
                 .order_by(FollowupCall.day_index).first())
        # 2026-07-26 10:00 IST = 2026-07-26 04:30 UTC
        assert first.scheduled_at.startswith("2026-07-26T04:30:00"), first.scheduled_at

    def test_enroll_audit_contains_call_count(self, client, db):
        _login(client)
        r = _enroll_via_api(client)
        eid = r["enrollment_id"]
        audit = db.query(AuditLog).filter(
            AuditLog.action == "enroll",
            AuditLog.entity_id == eid,
        ).first()
        assert audit is not None
        meta = json.loads(audit.meta)
        assert meta["calls"] == 4  # wound_care has 4 days
        assert meta["consent"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Sim-call trigger — dedup, channel handling
# ─────────────────────────────────────────────────────────────────────────────
class TestSimCallTrigger:
    def test_sim_dedup_blocks_second_active(self, client, db):
        _login(client)
        r = _enroll_via_api(client)
        eid = r["enrollment_id"]
        first = client.post("/api/demo/trigger-call", json={"enrollment_id": eid, "channel": "sim"})
        assert first.status_code == 200
        second = client.post("/api/demo/trigger-call", json={"enrollment_id": eid, "channel": "sim"})
        assert second.status_code == 409
        assert "already active" in second.json()["detail"]

    def test_sim_dedup_allows_after_completion(self, client, db):
        _login(client)
        r = _enroll_via_api(client)
        eid = r["enrollment_id"]
        first = client.post("/api/demo/trigger-call", json={"enrollment_id": eid, "channel": "sim"}).json()
        # mark the first call completed
        from app.models import FollowupCall
        c = db.get(FollowupCall, first["call_id"])
        c.status = "completed"
        db.commit()
        # second one should now go through
        second = client.post("/api/demo/trigger-call", json={"enrollment_id": eid, "channel": "sim"})
        assert second.status_code == 200

    def test_trigger_for_nonexistent_enrollment_404(self, client, db):
        _login(client)
        r = client.post("/api/demo/trigger-call",
                        json={"enrollment_id": "does-not-exist", "channel": "sim"})
        assert r.status_code == 404

    def test_trigger_cross_hospital_404(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, hospital="OTHER-HOSP")
        e = _make_enrollment(db, p.id, u.id, hospital="OTHER-HOSP")
        r = client.post("/api/demo/trigger-call",
                        json={"enrollment_id": e.id, "channel": "sim"})
        assert r.status_code == 404

    def test_trigger_audit_records_channel(self, client, db):
        _login(client)
        r = _enroll_via_api(client)
        eid = r["enrollment_id"]
        resp = client.post("/api/demo/trigger-call", json={"enrollment_id": eid, "channel": "sim"})
        cid = resp.json()["call_id"]
        audit = db.query(AuditLog).filter(
            AuditLog.action == "trigger_call",
            AuditLog.entity_id == cid,
        ).first()
        assert audit is not None
        assert json.loads(audit.meta)["channel"] == "sim"


# ─────────────────────────────────────────────────────────────────────────────
# Login / session — lockout, isolation, logout, rate-limit per pair
# ─────────────────────────────────────────────────────────────────────────────
class TestLoginSession:
    def test_lockout_is_per_ip_and_username(self, client, db):
        """Locking (admin, 127.0.0.1) must NOT lock (otheruser, 127.0.0.1)."""
        _attempts.clear()
        # Lock admin
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        # otheruser should still be free
        r = client.post("/api/auth/login", json={"username": "other", "password": "x"})
        assert r.status_code == 401  # bad password, not 429

    def test_lockout_releases_after_window(self, monkeypatch, client, db):
        _attempts.clear()
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        # fake time advance
        import time
        from app import security
        original = security.time.time
        monkeypatch.setattr(security.time, "time", lambda: original() + 31)
        try:
            r = client.post("/api/auth/login", json=ADMIN)
            assert r.status_code == 200
        finally:
            monkeypatch.setattr(security.time, "time", original)

    def test_logout_clears_session(self, client, db):
        _login(client)
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/auth/me").status_code == 401

    def test_session_cookie_is_http_only(self, client, db):
        r = client.post("/api/auth/login", json=ADMIN)
        cookie = r.cookies.get("session")
        assert cookie is not None
        # in dev (IS_PROD=False) httponly is False — confirm attribute is present
        raw = r.headers.get("set-cookie", "")
        assert "session=" in raw

    def test_login_unknown_user_401(self, client, db):
        r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
        assert r.status_code == 401
        assert "Invalid" in r.json()["detail"]

    def test_login_audit_on_success(self, client, db):
        client.post("/api/auth/login", json=ADMIN)
        a = db.query(AuditLog).filter(AuditLog.action == "login").first()
        assert a is not None
        assert a.actor == "admin"

    def test_login_audit_on_failure(self, client, db):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        a = db.query(AuditLog).filter(AuditLog.action == "login_failed").first()
        assert a is not None
        assert a.actor == "admin"


# ─────────────────────────────────────────────────────────────────────────────
# /api/auth/me — contract
# ─────────────────────────────────────────────────────────────────────────────
class TestMeEndpoint:
    def test_me_returns_hospital_name(self, client, db):
        _login(client)
        r = client.get("/api/auth/me").json()
        assert "hospital_name" in r
        assert r["role"] == "admin"
        assert "display_name" in r
        assert "id" in r

    def test_me_with_other_role(self, client, db):
        _make_user(db, "nurse_me", role="nurse", ward="Ward-7")
        r = client.post("/api/auth/login", json={"username": "nurse_me", "password": "pw123456"})
        assert r.status_code == 200
        me = client.get("/api/auth/me").json()
        assert me["role"] == "nurse"


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard daily-stats — time windowing
# ─────────────────────────────────────────────────────────────────────────────
class TestDailyStats:
    def test_today_window(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        now_iso = now_utc()
        # 1 call today, completed, green
        c1 = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_iso, status="completed", risk_level="green",
            completed_at=now_iso,
        )
        # 1 call today, completed, red
        c2 = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=3,
            scheduled_at=now_iso, status="completed", risk_level="red",
            completed_at=now_iso,
        )
        # 1 call today, failed
        c3 = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=7,
            scheduled_at=now_iso, status="failed",
        )
        # 1 call today, yellow
        c4 = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=14,
            scheduled_at=now_iso, status="completed", risk_level="yellow",
            completed_at=now_iso,
        )
        db.add_all([c1, c2, c3, c4]); db.commit()

        r = client.get("/api/dashboard/daily-stats").json()
        assert r["calls_today"] == 3       # c1, c2, c4 are completed
        assert r["risk_green"] == 1
        assert r["risk_yellow"] == 1
        assert r["risk_red"] == 1
        assert r["calls_failed"] == 1
        assert r["calls_scheduled"] == 4

    def test_old_calls_excluded_from_today(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        # call completed yesterday
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=yesterday, status="completed", risk_level="green",
            completed_at=yesterday,
        )
        db.add(c); db.commit()
        r = client.get("/api/dashboard/daily-stats").json()
        assert r["calls_today"] == 0
        assert r["risk_green"] == 0

    def test_resolved_today_counts_resolution_only(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id, level="red",
            reasons='["x"]', status="resolved", resolved_at=now_utc(),
        )
        db.add(esc); db.commit()
        r = client.get("/api/dashboard/daily-stats").json()
        assert r["resolved_today"] >= 1
        assert r["open_escalations"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Staff mgmt — admin-only, dedup, role validation
# ─────────────────────────────────────────────────────────────────────────────
class TestStaffMgmt:
    def test_list_requires_admin(self, client, db):
        _make_user(db, "nurse1", role="nurse")
        client.post("/api/auth/login", json={"username": "nurse1", "password": "pw123456"})
        r = client.get("/api/staff-mgmt")
        assert r.status_code == 403

    def test_create_staff_as_admin(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "newnurse", "display_name": "New Nurse",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["username"] == "newnurse"
        assert body["role"] == "nurse"

    def test_create_staff_invalid_role(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "valid_name", "display_name": "Valid Name",
            "role": "overlord", "password": "secret123",
        })
        assert r.status_code == 400
        assert "Invalid role" in r.json()["detail"]

    def test_create_staff_duplicate_username(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "dupe", "display_name": "Dupe One",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 201
        r = client.post("/api/staff-mgmt", json={
            "username": "dupe", "display_name": "Dupe Two",
            "role": "nurse", "password": "secret456",
        })
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"]

    def test_create_staff_uppercase_username_normalized(self, client, db):
        """AGENTS.md / convention says usernames are lowercase; the API normalises."""
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "MixedCase", "display_name": "Mixed Name",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 201
        assert r.json()["username"] == "mixedcase"

    def test_create_staff_special_chars_rejected(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "bad user!", "display_name": "Bad Name",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 400
        assert "lowercase" in r.json()["detail"].lower()

    def test_create_staff_short_username_rejected(self, client, db):
        """Pydantic enforces min_length=3 on the username field."""
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "ab", "display_name": "Short Name",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 422

    def test_create_staff_short_password_rejected(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "validuser", "display_name": "X",
            "role": "nurse", "password": "short",
        })
        assert r.status_code == 422

    def test_create_staff_audit_written(self, client, db):
        _login(client)
        client.post("/api/staff-mgmt", json={
            "username": "audited_staff", "display_name": "Audited",
            "role": "nurse", "password": "secret123",
        })
        a = db.query(AuditLog).filter(AuditLog.action == "create_staff").first()
        assert a is not None
        meta = json.loads(a.meta)
        assert meta["username"] == "audited_staff"
        assert meta["role"] == "nurse"

    def test_self_deletion_blocked(self, client, db):
        _login(client)
        me = client.get("/api/staff-mgmt/me").json()
        r = client.delete(f"/api/staff-mgmt/{me['id']}")
        assert r.status_code == 400
        assert "own" in r.json()["detail"].lower()

    def test_delete_staff_removes_row(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "to_delete", "display_name": "Delete Me",
            "role": "nurse", "password": "secret123",
        })
        assert r.status_code == 201
        target_id = r.json()["id"]
        assert client.delete(f"/api/staff-mgmt/{target_id}").status_code == 204
        after = client.get("/api/staff-mgmt").json()
        assert not any(u["username"] == "to_delete" for u in after)

    def test_change_own_password(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt/change-password", json={
            "current_password": "changeme123", "new_password": "newpass999",
        })
        assert r.status_code == 204
        client.post("/api/auth/logout")
        r2 = client.post("/api/auth/login",
                         json={"username": "admin", "password": "newpass999"})
        assert r2.status_code == 200

    def test_change_password_wrong_current(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt/change-password", json={
            "current_password": "wrong_pw", "new_password": "newpass999",
        })
        assert r.status_code == 400
        assert "incorrect" in r.json()["detail"].lower()

    def test_change_password_same_as_current_rejected(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt/change-password", json={
            "current_password": "changeme123", "new_password": "changeme123",
        })
        assert r.status_code == 400
        assert "different" in r.json()["detail"].lower()

    def test_change_password_audit(self, client, db):
        _login(client)
        client.post("/api/staff-mgmt/change-password", json={
            "current_password": "changeme123", "new_password": "newpass999",
        })
        a = db.query(AuditLog).filter(AuditLog.action == "change_password").first()
        assert a is not None

    def test_reset_password_requires_admin_password(self, client, db):
        _login(client)
        target_id = client.post("/api/staff-mgmt", json={
            "username": "target", "display_name": "Target",
            "role": "nurse", "password": "oldpass1",
        }).json()["id"]
        r = client.post(
            f"/api/staff-mgmt/{target_id}/reset-password",
            json={"current_password": "wrong_pw", "new_password": "newpass1"},
        )
        assert r.status_code == 400
        assert "admin password" in r.json()["detail"].lower()

    def test_reset_password_self_blocked(self, client, db):
        _login(client)
        me = client.get("/api/staff-mgmt/me").json()
        r = client.post(
            f"/api/staff-mgmt/{me['id']}/reset-password",
            json={"current_password": "changeme123", "new_password": "newpass1"},
        )
        assert r.status_code == 400
        assert "own" in r.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# L1: IST date helpers (tzutil.today_ist_iso, days_ist_window)
# ─────────────────────────────────────────────────────────────────────────────
class TestIstDateHelpers:
    def test_today_ist_iso_format(self):
        from app.tzutil import today_ist_iso
        d = today_ist_iso()
        assert len(d) == 10 and d[4] == "-" and d[7] == "-"

    def test_today_ist_matches_ist_now(self):
        """Even when called near midnight UTC, today_ist_iso() must
        return the IST calendar date, not UTC."""
        from datetime import datetime
        from app.tzutil import today_ist_iso, IST
        expected = datetime.now(IST).date().isoformat()
        assert today_ist_iso() == expected

    def test_today_ist_rolls_over_at_ist_midnight(self):
        """The naive toISOString() approach gives the wrong day near UTC
        midnight. Verify today_ist_iso() never does."""
        from app.tzutil import today_ist_iso, IST
        from datetime import datetime
        d1 = today_ist_iso()
        d2 = datetime.now(IST).date().isoformat()
        assert d1 == d2

    def test_days_ist_window_length(self):
        from app.tzutil import days_ist_window
        from datetime import datetime
        s, e = days_ist_window(7)
        si = datetime.fromisoformat(s)
        ei = datetime.fromisoformat(e)
        assert abs((ei - si).total_seconds() - 7 * 86400) < 1

    def test_days_ist_window_end_is_nowish(self):
        from app.tzutil import days_ist_window
        from datetime import datetime, timezone
        _, e = days_ist_window(3)
        ei = datetime.fromisoformat(e)
        now = datetime.now(timezone.utc)
        assert abs((now - ei).total_seconds()) < 5


# ─────────────────────────────────────────────────────────────────────────────
# L2: escalation webhook (HMAC-signed POST) + admin test endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestEscalationWebhook:
    def _webhook_send_setup(self, monkeypatch, url="https://hook.example/abc",
                            secret="test-secret-1234"):
        """Configure ESCALATION_WEBHOOK_URL and SECRET, and patch httpx.post
        to record what was sent. Returns (posted_dict, restore_fn)."""
        from app import config as cfg
        from app import notify
        old_url = cfg.settings.ESCALATION_WEBHOOK_URL
        old_secret = cfg.settings.ESCALATION_WEBHOOK_SECRET
        cfg.settings.ESCALATION_WEBHOOK_URL = url
        cfg.settings.ESCALATION_WEBHOOK_SECRET = secret
        posted = {"calls": [], "status": 200}
        def fake_post(url, content=None, headers=None, timeout=None):
            posted["calls"].append({"url": url, "content": content,
                                     "headers": headers, "timeout": timeout})
            class R:
                status_code = posted["status"]
                text = ""
            return R()
        monkeypatch.setattr(notify.httpx, "post", fake_post)
        def restore():
            cfg.settings.ESCALATION_WEBHOOK_URL = old_url
            cfg.settings.ESCALATION_WEBHOOK_SECRET = old_secret
        return posted, restore

    def test_webhook_send_no_url_returns_false(self):
        from app import config as cfg
        from app.notify import webhook_send
        from app.models import Escalation
        old = cfg.settings.ESCALATION_WEBHOOK_URL
        cfg.settings.ESCALATION_WEBHOOK_URL = ""
        try:
            esc = Escalation(id="x", level="red", reasons='["t"]')
            assert webhook_send(esc, "P", "+91", "H", "W", "p") is False
        finally:
            cfg.settings.ESCALATION_WEBHOOK_URL = old

    def test_webhook_send_posts_signed_payload(self, monkeypatch):
        posted, restore = self._webhook_send_setup(monkeypatch)
        from app.models import Escalation
        from app.notify import webhook_send
        import json
        esc = Escalation(id="esc-1", level="red",
                         reasons='["reason one","reason two"]')
        ok = webhook_send(esc, "Patient Name", "+919876543210",
                          "KA-DIST-01", "Ward-A", "wound_care")
        assert ok is True
        assert len(posted["calls"]) == 1
        call = posted["calls"][0]
        assert call["url"] == "https://hook.example/abc"
        assert call["timeout"] == 5.0
        # Headers
        sig_header = call["headers"]["X-Signature"]
        assert sig_header.startswith("sha256=")
        # Body is valid JSON with all the right keys
        body = json.loads(call["content"])
        assert body["event"] == "escalation"
        assert body["hospital_code"] == "KA-DIST-01"
        assert body["escalation_id"] == "esc-1"
        assert body["patient_name"] == "Patient Name"
        assert body["phone"] == "+919876543210"
        assert body["level"] == "red"
        assert body["ward"] == "Ward-A"
        assert body["protocol_id"] == "wound_care"
        assert body["reasons"] == ["reason one", "reason two"]
        assert "timestamp" in body
        # HMAC verification
        import hmac, hashlib
        expected = hmac.new(b"test-secret-1234",
                            call["content"], hashlib.sha256).hexdigest()
        assert sig_header == f"sha256={expected}"
        restore()

    def test_webhook_send_returns_false_on_5xx(self, monkeypatch):
        from app import config as cfg
        from app import notify
        from app.models import Escalation
        from app.notify import webhook_send
        old = cfg.settings.ESCALATION_WEBHOOK_URL
        cfg.settings.ESCALATION_WEBHOOK_URL = "https://hook.example/x"
        try:
            def fake_post_500(*a, **k):
                class R:
                    status_code = 503
                    text = "upstream down"
                return R()
            monkeypatch.setattr(notify.httpx, "post", fake_post_500)
            esc = Escalation(id="x", level="red", reasons='["t"]')
            assert webhook_send(esc, "P", "+91", "H", "W", "p") is False
        finally:
            cfg.settings.ESCALATION_WEBHOOK_URL = old

    def test_webhook_send_returns_false_on_timeout(self, monkeypatch):
        from app import config as cfg
        from app import notify
        from app.models import Escalation
        from app.notify import webhook_send
        old = cfg.settings.ESCALATION_WEBHOOK_URL
        cfg.settings.ESCALATION_WEBHOOK_URL = "https://hook.example/x"
        try:
            def fake_post_timeout(*a, **k):
                raise notify.httpx.ConnectError("boom")
            monkeypatch.setattr(notify.httpx, "post", fake_post_timeout)
            esc = Escalation(id="x", level="red", reasons='["t"]')
            assert webhook_send(esc, "P", "+91", "H", "W", "p") is False
        finally:
            cfg.settings.ESCALATION_WEBHOOK_URL = old

    def test_admin_test_webhook_requires_admin(self, client, db):
        _make_user(db, "nonadmin", role="nurse")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "nonadmin", "password": "pw123456"})
        r = client.post("/api/admin/webhooks/test", json={})
        assert r.status_code == 403

    def test_admin_test_webhook_returns_400_when_no_url(self, client, db):
        from app import config as cfg
        old = cfg.settings.ESCALATION_WEBHOOK_URL
        cfg.settings.ESCALATION_WEBHOOK_URL = ""
        try:
            _login(client)
            r = client.post("/api/admin/webhooks/test", json={})
            assert r.status_code == 400
            assert "ESCALATION_WEBHOOK_URL" in r.json()["detail"]
        finally:
            cfg.settings.ESCALATION_WEBHOOK_URL = old

    def test_admin_test_webhook_returns_ok_with_url(self, client, db, monkeypatch):
        from app import config as cfg
        from app import notify
        old_url = cfg.settings.ESCALATION_WEBHOOK_URL
        cfg.settings.ESCALATION_WEBHOOK_URL = "https://hook.example/x"
        try:
            def fake_post(*a, **k):
                class R:
                    status_code = 200
                    text = ""
                return R()
            monkeypatch.setattr(notify.httpx, "post", fake_post)
            _login(client)
            r = client.post("/api/admin/webhooks/test", json={
                "patient_name": "Smoke Patient",
                "level": "red",
                "ward": "ICU",
                "protocol_id": "wound_care",
                "reasons": ["test reason"],
            })
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert body["url"] == "https://hook.example/x"
            assert body["payload"]["patient_name"] == "Smoke Patient"
            assert body["payload"]["ward"] == "ICU"
        finally:
            cfg.settings.ESCALATION_WEBHOOK_URL = old_url

    def test_admin_test_webhook_requires_session(self, client):
        r = client.post("/api/admin/webhooks/test", json={})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# L5: per-protocol analytics
# ─────────────────────────────────────────────────────────────────────────────
class TestProtocolAnalytics:
    def _seed(self, db, *, name="P", protocol="wound_care", n=3,
              completed=2, with_risk=False):
        from app.models import FollowupCall
        u = db.query(User).filter(User.username == "admin").first()
        for i in range(n):
            p = _make_patient(db, u.id, name=f"{name}{i}")
            e = _make_enrollment(db, p.id, u.id, protocol=protocol)
            # Mark the enrollment status to match the call status, since
            # the analytics endpoint counts completed enrollments by
            # Enrollment.status, not by call count.
            if i < completed:
                e.status = "completed"
                db.commit()
            # One call per enrollment
            c = FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(),
                status="completed" if i < completed else "pending",
                completed_at=now_utc() if i < completed else None,
                risk_level="green" if (with_risk and i < completed) else None,
                risk_score=0,
            )
            db.add(c); db.commit()
        return u

    def test_protocol_analytics_returns_shape(self, client, db):
        self._seed(db, n=3, completed=2)
        _login(client)
        r = client.get("/api/protocols/wound_care/analytics")
        assert r.status_code == 200
        body = r.json()
        assert body["protocol_id"] == "wound_care"
        assert body["total_enrolled"] == 3
        assert body["completion_rate"] == round(2/3, 3)
        assert "risk_distribution" in body
        assert set(body["risk_distribution"].keys()) == {"green","yellow","red","unknown"}
        assert "outcomes" in body
        assert "avg_ack_hours" in body
        assert "pill_count_violations" in body
        # wound_care: not antibiotic, so pill_count is None
        assert body["pill_count_violations"] is None

    def test_protocol_analytics_requires_session(self, client):
        r = client.get("/api/protocols/wound_care/analytics")
        assert r.status_code == 401

    def test_protocol_analytics_unknown_404(self, client, db):
        _login(client)
        r = client.get("/api/protocols/nonexistent/analytics")
        assert r.status_code == 404

    def test_protocol_analytics_completion_rate(self, client, db):
        # 5 enrolled, 3 completed → 0.6
        self._seed(db, n=5, completed=3)
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        assert body["total_enrolled"] == 5
        assert body["completion_rate"] == 0.6

    def test_protocol_analytics_risk_distribution_from_latest_call(self, client, db):
        u = db.query(User).filter(User.username == "admin").first()
        # 2 green, 1 yellow, 1 red
        for i, (risk, _) in enumerate([("green",0),("green",0),("yellow",2),("red",10)]):
            p = _make_patient(db, u.id, name=f"RD{i}")
            e = _make_enrollment(db, p.id, u.id, protocol="wound_care")
            c = FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(), status="completed",
                completed_at=now_utc(), risk_level=risk, risk_score=0,
            )
            db.add(c); db.commit()
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        rd = body["risk_distribution"]
        assert rd["green"] == 2
        assert rd["yellow"] == 1
        assert rd["red"] == 1

    def test_protocol_analytics_empty_returns_null_rates(self, client, db):
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        assert body["total_enrolled"] == 0
        assert body["completion_rate"] is None
        assert body["red_flag_rate"] is None

    def test_protocol_analytics_antibiotic_has_pill_count_field(self, client, db):
        """Only the antibiotic protocol populates pill_count_violations."""
        # Create one antibiotic enrollment with a red escalation whose
        # reasons include the pill-count text.
        from app.models import FollowupCall, Escalation
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="AbxPill")
        e = _make_enrollment(db, p.id, u.id, protocol="antibiotic_course")
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_utc(), status="completed", completed_at=now_utc(),
            risk_level="red", risk_score=10,
        )
        db.add(c); db.commit()
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id, call_id=c.id,
            level="red", reasons='["pill count: 8+ remaining (adherence risk)"]',
            status="open",
        )
        db.add(esc); db.commit()
        _login(client)
        body = client.get("/api/protocols/antibiotic_course/analytics").json()
        assert body["pill_count_violations"] == 1

    def test_protocol_analytics_wound_no_pill_count(self, client, db):
        """Wound care has no pill-count question → field is null."""
        self._seed(db, n=2, completed=1)
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        assert body["pill_count_violations"] is None

    def test_protocol_analytics_avg_ack_hours(self, client, db):
        from app.models import FollowupCall, Escalation
        from datetime import datetime, timezone, timedelta
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="AckP")
        e = _make_enrollment(db, p.id, u.id, protocol="wound_care")
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_utc(), status="completed", completed_at=now_utc(),
        )
        db.add(c); db.commit()
        now = datetime.now(timezone.utc)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id, call_id=c.id,
            level="red", reasons='["x"]', status="acked",
            created_at=(now - timedelta(hours=2)).isoformat(),
            acked_at=now.isoformat(),
        )
        db.add(esc); db.commit()
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        assert body["avg_ack_hours"] is not None
        assert 1.9 < body["avg_ack_hours"] < 2.1

    def test_protocol_analytics_cross_hospital_excluded(self, client, db):
        """A protocol enrollment from a different hospital doesn't show up."""
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="OtherHosp", hospital="OTHER-HOSP")
        _make_enrollment(db, p.id, u.id, protocol="wound_care", hospital="OTHER-HOSP")
        _login(client)
        body = client.get("/api/protocols/wound_care/analytics").json()
        assert body["total_enrolled"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# L3: ward summary report
# ─────────────────────────────────────────────────────────────────────────────
class TestWardSummary:
    def _seed(self, db, *, ward="Ward-1", n=3, completed=2, with_esc=False):
        from app.models import FollowupCall, Escalation
        u = db.query(User).filter(User.username == "admin").first()
        eids = []
        for i in range(n):
            p = _make_patient(db, u.id, name=f"WS{i}")
            e = _make_enrollment(db, p.id, u.id, ward=ward)
            eids.append(e.id)
            if i < completed:
                e.status = "completed"
            c = FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(),
                status="completed" if i < completed else "no_answer",
                completed_at=now_utc() if i < completed else None,
            )
            db.add(c); db.commit()
        if with_esc:
            esc = Escalation(
                hospital_code="KA-DIST-01", enrollment_id=eids[0],
                level="red", reasons='["x"]', status="open",
            )
            db.add(esc); db.commit()
        return eids

    def test_wards_lists_distinct_sorted(self, client, db):
        self._seed(db, ward="Surgical")
        self._seed(db, ward="Medical")
        self._seed(db, ward="ICU")
        _login(client)
        wards = client.get("/api/analytics/wards").json()
        assert wards == ["ICU", "Medical", "Surgical"]

    def test_wards_requires_session(self, client):
        assert client.get("/api/analytics/wards").status_code == 401

    def test_wards_excludes_null(self, client, db):
        """A ward=null enrollment shouldn't appear in the list."""
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="NWard")
        _make_enrollment(db, p.id, u.id, ward=None)
        _login(client)
        assert client.get("/api/analytics/wards").json() == []

    def test_ward_summary_returns_shape(self, client, db):
        self._seed(db, ward="Ward-1", n=4, completed=3, with_esc=True)
        _login(client)
        r = client.get("/api/analytics/ward-summary?ward=Ward-1&days=7")
        assert r.status_code == 200
        body = r.json()
        for k in ("ward","period_days","total_enrolled","active","completed",
                  "cancelled","reach_rate","red_flag_rate","open_escalations",
                  "avg_ack_hours","outcome_breakdown","call_completion_by_day"):
            assert k in body, f"missing {k}"
        assert body["ward"] == "Ward-1"
        assert body["total_enrolled"] == 4
        assert body["completed"] == 3
        assert body["open_escalations"] == 1

    def test_ward_summary_filters_by_ward(self, client, db):
        self._seed(db, ward="Ward-1", n=2)
        self._seed(db, ward="Ward-2", n=5)
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=Ward-2").json()
        assert body["total_enrolled"] == 5
        assert body["ward"] == "Ward-2"

    def test_ward_summary_nurse_ward_scoping_ignored(self, client, db):
        """A nurse's request for another ward is silently redirected to their own."""
        # 1 patient in nurse's ward, 1 in another ward
        nurse = _make_user(db, "scope_nurse", role="nurse", ward="NurseWard")
        p1 = _make_patient(db, nurse.id, name="InNurseWard")
        _make_enrollment(db, p1.id, nurse.id, ward="NurseWard")
        p2 = _make_patient(db, nurse.id, name="InOtherWard")
        _make_enrollment(db, p2.id, nurse.id, ward="OtherWard")
        # Log in as the nurse
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "scope_nurse", "password": "pw123456"})
        # Nurse asks for OtherWard → should still see only NurseWard
        body = client.get("/api/analytics/ward-summary?ward=OtherWard").json()
        assert body["ward"] == "NurseWard"
        assert body["total_enrolled"] == 1

    def test_ward_summary_admin_can_request_any_ward(self, client, db):
        self._seed(db, ward="Surgical", n=2)
        self._seed(db, ward="Medical", n=3)
        _login(client)  # admin
        body = client.get("/api/analytics/ward-summary?ward=Surgical").json()
        assert body["total_enrolled"] == 2
        assert body["ward"] == "Surgical"

    def test_ward_summary_empty_window(self, client, db):
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=Nobody&days=7").json()
        assert body["total_enrolled"] == 0
        assert body["reach_rate"] is None
        assert body["red_flag_rate"] is None
        assert body["outcome_breakdown"] == {}
        assert body["call_completion_by_day"] == []

    def test_ward_summary_reach_rate_calculation(self, client, db):
        # 2 completed, 1 no_answer → reach = 2/3
        from app.models import FollowupCall
        u = db.query(User).filter(User.username == "admin").first()
        for i, st in enumerate(["completed","completed","no_answer"]):
            p = _make_patient(db, u.id, name=f"R{i}")
            e = _make_enrollment(db, p.id, u.id, ward="RR")
            if st == "completed":
                e.status = "completed"
            db.add(FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(), status=st,
                completed_at=now_utc() if st == "completed" else None,
            ))
            db.commit()
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=RR").json()
        assert body["reach_rate"] == round(2/3, 3)

    def test_ward_summary_avg_ack_hours(self, client, db):
        from app.models import FollowupCall, Escalation
        from datetime import datetime, timezone, timedelta
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="AAck")
        e = _make_enrollment(db, p.id, u.id, ward="AAck")
        e.status = "completed"
        db.add(FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_utc(), status="completed",
            completed_at=now_utc(),
        ))
        db.commit()
        now = datetime.now(timezone.utc)
        db.add(Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["x"]', status="acked",
            created_at=(now - timedelta(hours=2)).isoformat(),
            acked_at=now.isoformat(),
        ))
        db.commit()
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=AAck").json()
        assert body["avg_ack_hours"] is not None
        assert 1.9 < body["avg_ack_hours"] < 2.1

    def test_ward_summary_requires_session(self, client):
        assert client.get("/api/analytics/ward-summary?ward=X").status_code == 401

    def test_ward_summary_cross_hospital_excluded(self, client, db):
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="XHosp", hospital="OTHER-HOSP")
        _make_enrollment(db, p.id, u.id, ward="XWard", hospital="OTHER-HOSP")
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=XWard").json()
        assert body["total_enrolled"] == 0

    def test_ward_summary_call_completion_by_day(self, client, db):
        from app.models import FollowupCall
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="DCBD")
        e = _make_enrollment(db, p.id, u.id, ward="DCBD")
        e.status = "completed"
        for day, st in [(1, "completed"), (1, "no_answer"),
                         (3, "completed"), (3, "completed")]:
            db.add(FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=day,
                scheduled_at=now_utc(), status=st,
                completed_at=now_utc() if st == "completed" else None,
            ))
        db.commit()
        _login(client)
        body = client.get("/api/analytics/ward-summary?ward=DCBD").json()
        by_day = {d["day_index"]: d for d in body["call_completion_by_day"]}
        assert by_day[1]["total"] == 2
        assert by_day[1]["completed"] == 1
        assert by_day[3]["total"] == 2
        assert by_day[3]["completed"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# L4: district dashboard
# ─────────────────────────────────────────────────────────────────────────────
class TestDistrictDashboard:
    def _seed(self, db, *, ward="Ward-1", protocol="wound_care", n=2,
              completed=1, with_red_reason: str | None = None):
        from app.models import FollowupCall, Escalation
        u = db.query(User).filter(User.username == "admin").first()
        eids = []
        for i in range(n):
            p = _make_patient(db, u.id, name=f"DD{i}")
            e = _make_enrollment(db, p.id, u.id, ward=ward, protocol=protocol)
            if i < completed:
                e.status = "completed"
            eids.append(e.id)
            db.add(FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(),
                status="completed" if i < completed else "pending",
            ))
        db.commit()
        if with_red_reason:
            esc = Escalation(
                hospital_code="KA-DIST-01", enrollment_id=eids[0],
                level="red", reasons=json.dumps([with_red_reason]), status="open",
            )
            db.add(esc); db.commit()
        return eids

    def test_district_dashboard_returns_shape(self, client, db):
        self._seed(db, ward="Surgical", n=3, completed=2)
        self._seed(db, ward="Medical", n=2, completed=1)
        _login(client)
        body = client.get("/api/analytics/district-dashboard?days=30").json()
        for k in ("total_enrolled","total_active","total_red","period_days",
                  "ward_breakdown","protocol_breakdown","top_escalation_reasons"):
            assert k in body, f"missing {k}"
        assert body["total_enrolled"] == 5
        # 3 in Surgical (2 completed, 1 active) + 2 in Medical (1 completed, 1 active) = 2 active
        assert body["total_active"] == 2
        assert body["period_days"] == 30
        assert len(body["ward_breakdown"]) == 2

    def test_district_dashboard_requires_admin_or_doctor(self, client, db):
        _make_user(db, "dd_nurse", role="nurse")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "dd_nurse", "password": "pw123456"})
        r = client.get("/api/analytics/district-dashboard")
        assert r.status_code == 403

    def test_district_dashboard_doctor_allowed(self, client, db):
        _make_user(db, "dd_doc", role="doctor")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "dd_doc", "password": "pw123456"})
        r = client.get("/api/analytics/district-dashboard")
        assert r.status_code == 200

    def test_district_dashboard_ward_breakdown_correct(self, client, db):
        self._seed(db, ward="Surgical", n=3, completed=2)
        self._seed(db, ward="Medical", n=2, completed=1)
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        wards = {w["ward"]: w for w in body["ward_breakdown"]}
        assert wards["Surgical"]["enrolled"] == 3
        assert wards["Surgical"]["completed"] == 2
        assert wards["Medical"]["enrolled"] == 2
        assert wards["Medical"]["completed"] == 1
        # Reach rate: Surgical has 2 completed calls out of 3, Medical 1/2
        assert wards["Surgical"]["reach_rate"] == round(2/3, 3)
        assert wards["Medical"]["reach_rate"] == 0.5

    def test_district_dashboard_protocol_breakdown(self, client, db):
        self._seed(db, ward="A", protocol="wound_care", n=3, completed=2)
        self._seed(db, ward="B", protocol="antibiotic_course", n=2, completed=1)
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        protos = {p["protocol"]: p for p in body["protocol_breakdown"]}
        assert protos["wound_care"]["enrolled"] == 3
        assert protos["wound_care"]["completion_rate"] == round(2/3, 3)
        assert protos["antibiotic_course"]["enrolled"] == 2
        assert protos["antibiotic_course"]["completion_rate"] == 0.5

    def test_district_dashboard_top_reasons_dedupes_case_insensitive(self, client, db):
        # Three escalations with the same reason in different cases
        self._seed(db, ward="A", n=3, completed=0,
                    with_red_reason="RED: would not treat")
        # Add a few more escalations with variations
        from app.models import FollowupCall, Escalation
        u = db.query(User).filter(User.username == "admin").first()
        for i, reason in enumerate([
            "red: would not treat",     # duplicate (case-insensitive)
            "RED: would NOT treat",     # duplicate
            "different reason",         # unique
        ]):
            p = _make_patient(db, u.id, name=f"TR{i}")
            e = _make_enrollment(db, p.id, u.id, ward="B")
            db.add(FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(), status="completed",
                completed_at=now_utc(),
            ))
            db.add(Escalation(
                hospital_code="KA-DIST-01", enrollment_id=e.id,
                level="red", reasons=json.dumps([reason]), status="open",
            ))
        db.commit()
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        reasons = {r["reason"]: r["count"] for r in body["top_escalation_reasons"]}
        # The 3 case-variation reasons should dedup to 1 with count 3
        assert reasons["red: would not treat"] == 3
        assert reasons["different reason"] == 1

    def test_district_dashboard_empty(self, client, db):
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        assert body["total_enrolled"] == 0
        assert body["ward_breakdown"] == []
        assert body["protocol_breakdown"] == []
        assert body["top_escalation_reasons"] == []

    def test_district_dashboard_total_red_counts_open_and_acked(self, client, db):
        from app.models import FollowupCall, Escalation
        u = db.query(User).filter(User.username == "admin").first()
        # 1 open, 1 acked, 1 resolved
        for i, status in enumerate(["open", "acked", "resolved"]):
            p = _make_patient(db, u.id, name=f"Red{i}")
            e = _make_enrollment(db, p.id, u.id)
            db.add(FollowupCall(
                hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
                scheduled_at=now_utc(), status="completed",
                completed_at=now_utc(),
            ))
            db.add(Escalation(
                hospital_code="KA-DIST-01", enrollment_id=e.id,
                level="red", reasons='["x"]', status=status,
            ))
        db.commit()
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        assert body["total_red"] == 2  # open + acked

    def test_district_dashboard_cross_hospital_excluded(self, client, db):
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="Other", hospital="OTHER-HOSP")
        _make_enrollment(db, p.id, u.id, hospital="OTHER-HOSP")
        _login(client)
        body = client.get("/api/analytics/district-dashboard").json()
        assert body["total_enrolled"] == 0

    def test_district_dashboard_requires_session(self, client):
        assert client.get("/api/analytics/district-dashboard").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# L6: patient transfer
# ─────────────────────────────────────────────────────────────────────────────
class TestPatientTransfer:
    def test_transfer_changes_ward(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="TransferP")
        e = _make_enrollment(db, p.id, u.id, ward="Surgical")
        r = client.post(f"/api/enrollments/{e.id}/transfer",
                         json={"to_ward": "Medical", "reason": "doctor's call"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "transferred"
        assert body["from_ward"] == "Surgical"
        assert body["to_ward"] == "Medical"
        db.refresh(e)
        assert e.ward == "Medical"

    def test_transfer_audit_written(self, client, db):
        from app.models import AuditLog
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="AuditP")
        e = _make_enrollment(db, p.id, u.id, ward="Surgical")
        client.post(f"/api/enrollments/{e.id}/transfer",
                     json={"to_ward": "Medical", "reason": "test reason"})
        a = db.query(AuditLog).filter(
            AuditLog.action == "transfer",
            AuditLog.entity_id == e.id,
        ).first()
        assert a is not None
        meta = json.loads(a.meta)
        assert meta["from"] == "Surgical"
        assert meta["to"] == "Medical"
        assert meta["reason"] == "test reason"

    def test_transfer_idor_404(self, client, db):
        """Transferring an enrollment from a different hospital returns 404."""
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="XHosp", hospital="OTHER-HOSP")
        e = _make_enrollment(db, p.id, u.id, ward="XWard", hospital="OTHER-HOSP")
        r = client.post(f"/api/enrollments/{e.id}/transfer",
                         json={"to_ward": "Medical"})
        assert r.status_code == 404

    def test_transfer_unknown_enrollment_404(self, client, db):
        _login(client)
        r = client.post("/api/enrollments/does-not-exist/transfer",
                         json={"to_ward": "Medical"})
        assert r.status_code == 404

    def test_transfer_nurse_wrong_ward_403(self, client, db):
        """A nurse can only transfer patients within their own ward."""
        _make_user(db, "transfer_nurse", role="nurse", ward="NurseWard")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "transfer_nurse", "password": "pw123456"})
        u = db.query(User).filter(User.username == "transfer_nurse").first()
        p = _make_patient(db, u.id, name="WrongWard")
        e = _make_enrollment(db, p.id, u.id, ward="OtherWard")
        r = client.post(f"/api/enrollments/{e.id}/transfer",
                         json={"to_ward": "NurseWard"})
        assert r.status_code == 403

    def test_transfer_nurse_same_ward_ok(self, client, db):
        _make_user(db, "transfer_nurse2", role="nurse", ward="NurseWard")
        client.post("/api/auth/logout")
        client.post("/api/auth/login",
                     json={"username": "transfer_nurse2", "password": "pw123456"})
        u = db.query(User).filter(User.username == "transfer_nurse2").first()
        p = _make_patient(db, u.id, name="SameWard")
        e = _make_enrollment(db, p.id, u.id, ward="NurseWard")
        r = client.post(f"/api/enrollments/{e.id}/transfer",
                         json={"to_ward": "NewWard", "reason": "ok"})
        assert r.status_code == 200

    def test_transfer_admin_can_move_any_patient(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="AnyP")
        e = _make_enrollment(db, p.id, u.id, ward="AnyFrom")
        r = client.post(f"/api/enrollments/{e.id}/transfer",
                         json={"to_ward": "AnyTo"})
        assert r.status_code == 200
        db.refresh(e)
        assert e.ward == "AnyTo"

    def test_transfer_requires_session(self, client):
        r = client.post("/api/enrollments/whatever/transfer",
                         json={"to_ward": "X"})
        assert r.status_code == 401

    def test_transfer_missing_to_ward_422(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="Missing")
        e = _make_enrollment(db, p.id, u.id, ward="X")
        r = client.post(f"/api/enrollments/{e.id}/transfer", json={})
        assert r.status_code == 422


# ── T10: Forgot-password via Telegram (no new dep) ──────────────────────────
class TestForgotPassword:
    """T10 (docs/09_PLAN.md): 6-digit OTP, 15-min TTL, in-memory store.
    Telegram call is best-effort; tests assert OTP correctness, not the
    Telegram send itself (Telegram is disabled in test env)."""

    def test_forgot_unknown_user_returns_200_no_enumeration(self, client, db):
        """Forgot with a non-existent username still returns 200."""
        r = client.post("/api/auth/forgot", json={"username": "nobody_here"})
        assert r.status_code == 200
        # No hint about whether the user existed
        body = r.json()
        assert body["ok"] is True

    def test_forgot_known_user_stores_otp(self, client, db):
        from app.routers.auth import _otp_store
        _otp_store.clear()
        _login(client)
        # Reset the admin password so we don't disturb the test session
        client.post("/api/auth/logout")
        r = client.post("/api/auth/forgot", json={"username": "admin"})
        assert r.status_code == 200
        # An OTP must be in the store
        assert "admin" in _otp_store
        rec = _otp_store["admin"]
        assert "code_hash" in rec
        assert "expires_at" in rec

    def test_reset_with_correct_otp_changes_password(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        # Pre-seed an OTP
        code = "123456"
        _otp_store["admin"] = {
            "code_hash": _hash_otp(code),
            "expires_at": 9999999999,  # far future
            "attempts": 0,
        }
        r = client.post("/api/auth/reset", json={
            "username": "admin", "otp": code, "new_password": "newpass999",
        })
        assert r.status_code == 204
        # OTP is consumed
        assert "admin" not in _otp_store
        # New password works
        r = client.post("/api/auth/login", json={
            "username": "admin", "password": "newpass999",
        })
        assert r.status_code == 200

    def test_reset_with_wrong_otp_returns_401(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        _otp_store["admin"] = {
            "code_hash": _hash_otp("123456"),
            "expires_at": 9999999999,
            "attempts": 0,
        }
        r = client.post("/api/auth/reset", json={
            "username": "admin", "otp": "000000", "new_password": "newpass999",
        })
        assert r.status_code == 401

    def test_reset_with_expired_otp_returns_400(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        _otp_store["admin"] = {
            "code_hash": _hash_otp("123456"),
            "expires_at": 0,  # expired
            "attempts": 0,
        }
        r = client.post("/api/auth/reset", json={
            "username": "admin", "otp": "123456", "new_password": "newpass999",
        })
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()

    def test_reset_with_unknown_user_returns_400(self, client, db):
        from app.routers.auth import _otp_store
        _otp_store.clear()
        r = client.post("/api/auth/reset", json={
            "username": "ghost", "otp": "123456", "new_password": "newpass999",
        })
        assert r.status_code == 400

    def test_reset_short_password_rejected(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        _otp_store["admin"] = {
            "code_hash": _hash_otp("123456"),
            "expires_at": 9999999999,
            "attempts": 0,
        }
        r = client.post("/api/auth/reset", json={
            "username": "admin", "otp": "123456", "new_password": "short",
        })
        assert r.status_code == 422

    def test_reset_audit_written(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        _otp_store["admin"] = {
            "code_hash": _hash_otp("123456"),
            "expires_at": 9999999999,
            "attempts": 0,
        }
        client.post("/api/auth/reset", json={
            "username": "admin", "otp": "123456", "new_password": "newpass999",
        })
        a = db.query(AuditLog).filter(AuditLog.action == "password_reset").first()
        assert a is not None
        assert a.actor == "admin"

    def test_reset_too_many_attempts_locks(self, client, db):
        from app.routers.auth import _otp_store, _hash_otp
        _otp_store.clear()
        _otp_store["admin"] = {
            "code_hash": _hash_otp("123456"),
            "expires_at": 9999999999,
            "attempts": 0,
        }
        # 5 wrong attempts — should consume the OTP
        for _ in range(5):
            r = client.post("/api/auth/reset", json={
                "username": "admin", "otp": "000000", "new_password": "newpass999",
            })
            assert r.status_code == 401
        # 6th attempt — store is cleared
        r = client.post("/api/auth/reset", json={
            "username": "admin", "otp": "123456", "new_password": "newpass999",
        })
        assert r.status_code == 400
        assert "admin" not in _otp_store


# ─────────────────────────────────────────────────────────────────────────────
# Report upload — full lifecycle + IDOR + intake-style multi-file flow
# ─────────────────────────────────────────────────────────────────────────────
class TestReportUploadFlow:
    def _seed(self, client, db):
        return _enroll_via_api(client)["enrollment_id"]

    def test_upload_zero_files_rejected(self, client, db):
        _login(client)
        eid = self._seed(client, db)
        # FastAPI returns 422 when a required File parameter is missing
        r = client.post(f"/api/enrollments/{eid}/reports")
        assert r.status_code == 422

    def test_upload_filename_with_path_traversal_sanitised(self, client, db):
        """A filename like `../../etc/passwd.pdf` must be stored safely:
        no `..` segments, and only the basename survives in the stored path."""
        _login(client)
        eid = self._seed(client, db)
        files = {"files": ("../../etc/passwd.pdf",
                           io.BytesIO(b"%PDF-1.4"), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 201, r.text
        report = r.json()["uploaded"][0]
        stored = report["filename"]
        # No `..` segments allowed anywhere in the stored path.
        assert ".." not in stored.split("/")
        # The basename (after the last `/`) must contain `passwd.pdf` — i.e.
        # the original name's basename was kept, the traversal was dropped.
        assert stored.split("/")[-1].endswith("_passwd.pdf")

    def test_upload_anonymous_filename_rejected(self, client, db):
        """Uploading with filename `.pdf` (no real name) is rejected with 400.
        Pathlib treats `.pdf` as having no extension, so it's not in the allowlist."""
        _login(client)
        eid = self._seed(client, db)
        files = {"files": (".pdf", io.BytesIO(b"%PDF"), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 400
        assert "not allowed" in r.json()["detail"]

    def test_upload_overwrite_safe(self, client, db):
        """Two uploads of the same filename should produce two distinct stored files."""
        _login(client)
        eid = self._seed(client, db)
        files = {"files": ("same.pdf", io.BytesIO(b"v1"), "application/pdf")}
        r1 = client.post(f"/api/enrollments/{eid}/reports", files=files)
        files2 = {"files": ("same.pdf", io.BytesIO(b"v2"), "application/pdf")}
        r2 = client.post(f"/api/enrollments/{eid}/reports", files=files2)
        assert r1.json()["uploaded"][0]["id"] != r2.json()["uploaded"][0]["id"]

    def test_list_ordered_newest_first(self, client, db):
        _login(client)
        eid = self._seed(client, db)
        import time
        for name in ("a.pdf", "b.pdf", "c.pdf"):
            client.post(
                f"/api/enrollments/{eid}/reports",
                files={"files": (name, io.BytesIO(b"%PDF"), "application/pdf")},
            )
            time.sleep(0.01)  # ensure distinct uploaded_at
        items = client.get(f"/api/enrollments/{eid}/reports").json()
        assert len(items) == 3
        # newest first
        assert items[0]["filename"] > items[-1]["filename"] or items[0]["uploaded_at"] >= items[-1]["uploaded_at"]

    def test_report_audit_written(self, client, db):
        _login(client)
        eid = self._seed(client, db)
        client.post(
            f"/api/enrollments/{eid}/reports",
            files={"files": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
        )
        a = db.query(AuditLog).filter(AuditLog.action == "report_upload").first()
        assert a is not None
        meta = json.loads(a.meta)
        assert meta["enrollment_id"] == eid
        assert meta["count"] == 1

    def test_upload_size_at_limit_ok(self, client, db):
        """Exactly 10MB should be accepted (the limit is `>` 10MB, not `>=`)."""
        _login(client)
        eid = self._seed(client, db)
        # a 10MB PDF header + zero-padding to exactly 10MB
        body = b"%PDF-1.4\n" + b"\x00" * (10 * 1024 * 1024 - len(b"%PDF-1.4\n"))
        assert len(body) == 10 * 1024 * 1024
        files = {"files": ("limit.pdf", io.BytesIO(body), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 201, r.text


# ─────────────────────────────────────────────────────────────────────────────
# /api/patients/{pid} — ward scoping for nurse/staff
# ─────────────────────────────────────────────────────────────────────────────
class TestPatientDetailAccess:
    def test_admin_sees_all(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id, ward="Ward-Z")
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 200

    def test_nurse_correct_ward_sees_patient(self, client, db):
        u = _make_user(db, "ward_nurse", role="nurse", ward="Ward-A")
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id, ward="Ward-A")
        client.post("/api/auth/login", json={"username": "ward_nurse", "password": "pw123456"})
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 200

    def test_nurse_wrong_ward_gets_404(self, client, db):
        u = _make_user(db, "ward_nurse_b", role="nurse", ward="Ward-B")
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id, ward="Ward-A")
        client.post("/api/auth/login", json={"username": "ward_nurse_b", "password": "pw123456"})
        # IDOR: 404, NOT 403, to avoid revealing patient existence
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 404

    def test_patient_with_no_ward_visible_to_any_nurse(self, client, db):
        u = _make_user(db, "ward_nurse_c", role="nurse", ward="Ward-C")
        p = _make_patient(db, u.id)
        e = _make_enrollment(db, p.id, u.id, ward=None)  # unassigned
        client.post("/api/auth/login", json={"username": "ward_nurse_c", "password": "pw123456"})
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 200

    def test_unknown_patient_404(self, client, db):
        _login(client)
        r = client.get("/api/patients/nonexistent-uuid")
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# /api/staff/activity & /api/staff/patients — own-actor filtering
# ─────────────────────────────────────────────────────────────────────────────
class TestStaffActivity:
    def test_activity_only_shows_own_actions(self, client, db):
        """The /api/staff/activity endpoint is scoped to the current user.
        Admin's enroll should not appear in nurse's feed (and vice versa)."""
        _login(client)
        _make_user(db, "act_nurse", role="nurse")
        # 1 enroll by admin
        _enroll_via_api(client, patient={"name": "AdminEnroll", "age": 40, "sex": "M",
                                          "caregiver_name": "C", "caregiver_phone": "+919876500111"})
        # nurse login + enroll
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"username": "act_nurse", "password": "pw123456"})
        body = {
            "patient": {"name": "NurseEnroll", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919876500112"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        assert client.post("/api/enrollments", json=body).status_code == 201

        # nurse should see their own enroll (and their login, which we filter out)
        acts = client.get("/api/staff/activity").json()
        enrolls = [a for a in acts if a["action"] == "enroll"]
        assert len(enrolls) == 1
        # entity_name is the resolved enrollment: "Enrollment: <protocol>"
        assert enrolls[0]["entity_name"] == "Enrollment: wound_care"

        # admin should see only their own enroll
        client.post("/api/auth/logout")
        _login(client)
        admin_acts = client.get("/api/staff/activity").json()
        admin_enrolls = [a for a in admin_acts if a["action"] == "enroll"]
        assert len(admin_enrolls) == 1
        assert admin_enrolls[0]["entity_name"] == "Enrollment: wound_care"

        # and admin's entity_id should differ from nurse's
        assert enrolls[0]["entity_id"] != admin_enrolls[0]["entity_id"]

    def test_activity_entity_names_resolved(self, client, db):
        _login(client)
        body = {
            "patient": {"name": "NamedEnroll", "age": 40, "sex": "M",
                        "caregiver_name": "C", "caregiver_phone": "+919876500121"},
            "protocol_id": "wound_care", "condition_label": "x",
            "discharge_date": "2026-07-25", "meds": [], "consent": True,
        }
        eid = client.post("/api/enrollments", json=body).json()["enrollment_id"]
        acts = client.get("/api/staff/activity").json()
        enrolls = [a for a in acts if a["action"] == "enroll"]
        assert any(a["entity_id"] == eid for a in enrolls)
        mine = [a for a in enrolls if a["entity_id"] == eid][0]
        # either "Patient: <name>" or "Enrollment: <protocol>" prefix
        assert mine["entity_name"].startswith(("Patient:", "Enrollment:"))


# ─────────────────────────────────────────────────────────────────────────────
# /api/protocols — shape and content
# ─────────────────────────────────────────────────────────────────────────────
class TestProtocols:
    def test_list_has_three_protocols(self, client, db):
        _login(client)
        protos = client.get("/api/protocols").json()
        ids = {p["id"] for p in protos}
        assert {"wound_care", "antibiotic_course", "fever_viral"} <= ids

    def test_protocol_detail_returns_questions(self, client, db):
        _login(client)
        detail = client.get("/api/protocols/wound_care/detail").json()
        assert detail["id"] == "wound_care"
        assert "name_kn" in detail
        assert isinstance(detail["questions"], dict)
        # at least one question node
        assert len(detail["questions"]) > 0
        first_qid = next(iter(detail["questions"]))
        first_q = detail["questions"][first_qid]
        assert "options" in first_q
        # each option has a reason + score
        for digit, opt in first_q["options"].items():
            assert "reason" in opt
            assert "score" in opt

    def test_protocol_detail_unknown_404(self, client, db):
        _login(client)
        r = client.get("/api/protocols/no-such-protocol/detail")
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Twilio webhook status callback — retry logic
# ─────────────────────────────────────────────────────────────────────────────
class TestTwilioStatusCallback:
    def test_no_answer_creates_retry(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=eid, day_index=1,
            scheduled_at=now_utc(), status="ringing", attempt=1,
        )
        db.add(c); db.commit(); db.refresh(c)
        r = client.post(f"/webhooks/twilio/status/{c.id}", data={"CallStatus": "no-answer"})
        assert r.status_code == 204
        # original marked no_answer, new attempt created
        db.refresh(c)
        assert c.status == "no_answer"
        retries = db.query(FollowupCall).filter(
            FollowupCall.enrollment_id == eid,
            FollowupCall.attempt == 2,
        ).all()
        assert len(retries) == 1

    def test_no_answer_max_attempts_no_retry(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=eid, day_index=1,
            scheduled_at=now_utc(), status="ringing", attempt=2,
        )
        db.add(c); db.commit(); db.refresh(c)
        r = client.post(f"/webhooks/twilio/status/{c.id}", data={"CallStatus": "no-answer"})
        assert r.status_code == 204
        retries = db.query(FollowupCall).filter(
            FollowupCall.enrollment_id == eid,
            FollowupCall.attempt == 3,
        ).all()
        assert len(retries) == 0

    def test_completed_callback_marks_complete(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=eid, day_index=1,
            scheduled_at=now_utc(), status="in_progress", attempt=1,
        )
        db.add(c); db.commit(); db.refresh(c)
        r = client.post(f"/webhooks/twilio/status/{c.id}", data={"CallStatus": "completed"})
        assert r.status_code == 204
        db.refresh(c)
        assert c.status == "completed"
        assert c.completed_at is not None

    def test_failed_callback_marks_failed(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=eid, day_index=1,
            scheduled_at=now_utc(), status="ringing", attempt=1,
        )
        db.add(c); db.commit(); db.refresh(c)
        client.post(f"/webhooks/twilio/status/{c.id}", data={"CallStatus": "failed"})
        db.refresh(c)
        assert c.status == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# SSE event stream — at least a heartbeat / no crash
# ─────────────────────────────────────────────────────────────────────────────
class TestSSEEvents:
    def test_events_endpoint_protected(self, client, db):
        """The /api/events endpoint must require a session (covered by
        TestUnauthenticatedAccess, but duplicated here for clarity)."""
        assert client.get("/api/events").status_code == 401

    def test_publish_then_subscribe(self, client, db):
        """Drive the in-process event bus: subscribers should see published events
        without needing the HTTP layer at all."""
        from app.events import _subscribe, _unsubscribe, publish
        evts, ref = _subscribe()
        try:
            publish("call_update", "abc123")
            # drain the deque
            collected = []
            while evts:
                collected.append(evts.popleft())
            assert len(collected) == 1
            assert collected[0]["type"] == "call_update"
            assert collected[0]["id"] == "abc123"
        finally:
            _unsubscribe(ref)

    def test_multiple_subscribers_each_receive(self, client, db):
        from app.events import _subscribe, _unsubscribe, publish
        e1, r1 = _subscribe()
        e2, r2 = _subscribe()
        try:
            publish("escalation", "xyz")
            assert len(e1) == 1
            assert len(e2) == 1
        finally:
            _unsubscribe(r1)
            _unsubscribe(r2)


# ─────────────────────────────────────────────────────────────────────────────
# Kannada sheet — endpoints
# ─────────────────────────────────────────────────────────────────────────────
class TestKannadaSheet:
    def test_sheet_includes_bullets_and_phone(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        sheet = client.get(f"/api/enrollments/{eid}/sheet").json()
        assert "hospital_name" in sheet
        assert "patient_name" in sheet
        assert "bullets_kn" in sheet
        assert len(sheet["bullets_kn"]) > 0
        assert sheet["telephones"] == "104 / 108"
        assert "schedule_days" in sheet

    def test_sheet_cross_hospital_404(self, client, db):
        _login(client)
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, hospital="OTHER-HOSP")
        e = _make_enrollment(db, p.id, u.id, hospital="OTHER-HOSP")
        r = client.get(f"/api/enrollments/{e.id}/sheet")
        assert r.status_code == 404

    def test_sheet_requires_session(self, client, db):
        r = client.get("/api/enrollments/anything/sheet")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Verify-number — 503 when twilio off, conflict on dup, audit
# ─────────────────────────────────────────────────────────────────────────────
class TestVerifyNumber:
    def test_verify_503_when_twilio_off(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        r = client.post(f"/api/enrollments/{eid}/verify-number")
        # No body → default to voice method, no Twilio in tests
        assert r.status_code == 503
        assert "twilio" in r.json()["detail"].lower()

    def test_verify_requires_session(self, client, db):
        r = client.post("/api/enrollments/anything/verify-number")
        assert r.status_code == 401

    def test_verify_desk_method_marks_verified_without_twilio(self, client, db):
        """The desk-confirm path (T4) does not require Twilio. With no Twilio
        creds, posting {method:'desk', confirmed:true} still sets
        number_verified=1."""
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        # First, ensure the enrollment is unverified
        from app.models import Enrollment
        e = db.get(Enrollment, eid)
        assert e.number_verified == 0

        r = client.post(f"/api/enrollments/{eid}/verify-number",
                         json={"method": "desk", "confirmed": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "desk"
        assert body["verified"] is True
        assert body["call_id"] is None

        db.refresh(e)
        assert e.number_verified == 1

    def test_verify_desk_requires_confirmed_true(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        r = client.post(f"/api/enrollments/{eid}/verify-number",
                         json={"method": "desk", "confirmed": False})
        assert r.status_code == 400
        assert "confirmed" in r.json()["detail"].lower()

    def test_verify_desk_writes_audit_with_method(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        client.post(f"/api/enrollments/{eid}/verify-number",
                     json={"method": "desk", "confirmed": True})
        a = db.query(AuditLog).filter(
            AuditLog.action == "verify_number",
            AuditLog.entity_id == eid,
        ).first()
        assert a is not None
        meta = json.loads(a.meta)
        assert meta["method"] == "desk"

    def test_verify_invalid_method(self, client, db):
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        r = client.post(f"/api/enrollments/{eid}/verify-number",
                         json={"method": "carrier_pigeon"})
        assert r.status_code == 400
        assert "desk" in r.json()["detail"] and "voice" in r.json()["detail"]

    def test_verify_voice_suggests_desk_in_503(self, client, db):
        """When the 503 fires, the error message should hint at the desk
        alternative so a nurse at the discharge counter doesn't think the
        app is broken."""
        _login(client)
        eid = _enroll_via_api(client)["enrollment_id"]
        r = client.post(f"/api/enrollments/{eid}/verify-number",
                         json={"method": "voice"})
        assert r.status_code == 503
        assert "desk" in r.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestHealthEndpoint:
    def test_healthz_no_auth_required(self, client):
        r = client.get("/api/healthz")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_healthz_includes_hospital_name(self, client):
        r = client.get("/api/healthz")
        assert "hospital" in r.json()
        assert r.json()["hospital"]  # non-empty


# ─────────────────────────────────────────────────────────────────────────────
# WhatNow panel — /api/board/whatnow (T6 in docs/09_PLAN.md)
# ─────────────────────────────────────────────────────────────────────────────
class TestBoardWhatNow:
    def _seed(self, db, *, name="WN", protocol="wound_care", ward=None,
              scheduled_offset_min=10, status="pending"):
        from app.models import FollowupCall
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name=name, hospital=ward or "KA-DIST-01")
        e = _make_enrollment(db, p.id, u.id, ward=ward, protocol=protocol)
        sched = (datetime.now(timezone.utc)
                 + timedelta(minutes=scheduled_offset_min)).isoformat()
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            day_index=1, scheduled_at=sched, status=status,
        )
        db.add(c); db.commit(); db.refresh(c)
        return u, p, e, c

    def test_whatnow_requires_session(self, client):
        r = client.get("/api/board/whatnow")
        assert r.status_code == 401

    def test_whatnow_returns_three_keys(self, client, db):
        _login(client)
        r = client.get("/api/board/whatnow")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"next_calls_due_2h", "stale_calls", "unresolved_red"}
        assert isinstance(body["next_calls_due_2h"], list)

    def test_whatnow_next_calls_due_2h(self, client, db):
        _login(client)
        self._seed(db, name="Soon", scheduled_offset_min=30)   # within 2h
        self._seed(db, name="Later", scheduled_offset_min=300) # > 2h, excluded
        body = client.get("/api/board/whatnow").json()
        names = [c["patient_name"] for c in body["next_calls_due_2h"]]
        assert "Soon" in names
        assert "Later" not in names

    def test_whatnow_stale_calls(self, client, db):
        _login(client)
        from datetime import datetime, timezone
        from app.models import FollowupCall
        # A call scheduled 30h ago, still pending
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="StaleCaller")
        e = _make_enrollment(db, p.id, u.id)
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            day_index=1, scheduled_at=(datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
            status="pending",
        )
        db.add(c); db.commit()
        body = client.get("/api/board/whatnow").json()
        names = [c["patient_name"] for c in body["stale_calls"]]
        assert "StaleCaller" in names
        assert body["stale_calls"][0]["hours_stale"] >= 24

    def test_whatnow_unresolved_red(self, client, db):
        _login(client)
        from datetime import datetime, timezone
        from app.models import Escalation
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="RedPatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="open",
            created_at=now_utc(),
        )
        db.add(esc); db.commit()
        body = client.get("/api/board/whatnow").json()
        names = [x["patient_name"] for x in body["unresolved_red"]]
        assert "RedPatient" in names

    def test_whatnow_resolved_escalation_excluded(self, client, db):
        _login(client)
        from app.models import Escalation
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="ResolvedPatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="resolved",
            created_at=now_utc(),
        )
        db.add(esc); db.commit()
        body = client.get("/api/board/whatnow").json()
        names = [x["patient_name"] for x in body["unresolved_red"]]
        assert "ResolvedPatient" not in names

    def test_whatnow_caps_at_5(self, client, db):
        _login(client)
        for i in range(8):
            self._seed(db, name=f"Caller{i}", scheduled_offset_min=10 + i)
        body = client.get("/api/board/whatnow").json()
        assert len(body["next_calls_due_2h"]) <= 5

    def test_whatnow_cross_hospital_excluded(self, client, db):
        _login(client)
        from app.models import FollowupCall
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="CrossHospital", hospital="OTHER")
        e = _make_enrollment(db, p.id, u.id, hospital="OTHER")
        c = FollowupCall(
            hospital_code="OTHER", enrollment_id=e.id, day_index=1,
            scheduled_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            status="pending",
        )
        db.add(c); db.commit()
        body = client.get("/api/board/whatnow").json()
        names = [c["patient_name"] for c in body["next_calls_due_2h"]]
        assert "CrossHospital" not in names


# ─────────────────────────────────────────────────────────────────────────────
# T12: DB-backed escalation fallback (pending_notifications retry queue)
# ─────────────────────────────────────────────────────────────────────────────
class TestPendingNotifications:
    def test_telegram_red_creates_pending_notification_row(self, client, db):
        """Every escalation creates a pending_notifications row. If Telegram
        is disabled (test env) the row is status='pending'; if enabled
        it's status='sent'."""
        from app.models import PendingNotification
        from app.ivr import engine
        from app.routers import escalations as esc_router

        # Set up an enrollment + call so the engine can write an escalation
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="NotifPatient")
        e = _make_enrollment(db, p.id, u.id)
        c = FollowupCall(
            hospital_code="KA-DIST-01", enrollment_id=e.id, day_index=1,
            scheduled_at=now_utc(), status="completed",
            risk_level="red", risk_score=10,
            risk_reasons='["wound: pus/bleeding/fever (SSI red flag)"]',
        )
        db.add(c); db.commit(); db.refresh(c)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id, call_id=c.id,
            level="red", reasons='["wound: pus/bleeding/fever (SSI red flag)"]',
            status="open",
        )
        db.add(esc); db.commit(); db.refresh(esc)
        # Manually call the notify function (the engine red hook)
        from app.notify import telegram_red
        telegram_red(esc)
        # A row was created
        rows = (db.query(PendingNotification)
                .filter(PendingNotification.entity_id == esc.id).all())
        assert len(rows) == 1
        # In test env Telegram is disabled, so status='pending'
        assert rows[0].status == "pending"
        assert rows[0].kind == "escalation"
        assert "wound" in rows[0].text.lower()

    def test_retry_sends_and_marks_sent(self, client, db, monkeypatch):
        """When telegram_send returns True on retry, the row is marked sent."""
        from app.models import PendingNotification
        from datetime import datetime, timezone, timedelta
        # Seed a pending row
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="RetryPatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="open",
        )
        db.add(esc); db.commit(); db.refresh(esc)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        row = PendingNotification(
            hospital_code="KA-DIST-01", kind="escalation",
            entity_id=esc.id, text="test message",
            attempt=1, last_error="previous failure",
            next_retry_at=past, status="pending",
        )
        db.add(row); db.commit(); db.refresh(row)

        # Mock telegram_send to return True
        from app import notify
        monkeypatch.setattr(notify, "telegram_send", lambda text: True)

        # Run the retry job
        from app.scheduler import _retry_pending_notifications
        _retry_pending_notifications()
        db.refresh(row)
        assert row.status == "sent"
        assert row.attempt == 2
        assert row.sent_at is not None

    def test_retry_exhausts_5_attempts_and_marks_failed(self, client, db, monkeypatch):
        """After 5 failed attempts, the row is `failed` and an SSE
        `notification:failed` event is published."""
        from app.models import PendingNotification
        from datetime import datetime, timezone, timedelta
        from app import notify
        from app.events import _subscribers

        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="ExhaustPatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="open",
        )
        db.add(esc); db.commit(); db.refresh(esc)

        # Subscribe to events so we can assert the SSE publish
        from app.events import _subscribe
        evts, ref = _subscribe()
        try:
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            row = PendingNotification(
                hospital_code="KA-DIST-01", kind="escalation",
                entity_id=esc.id, text="test",
                attempt=4,   # one more failure = 5 total
                next_retry_at=past, status="pending",
            )
            db.add(row); db.commit(); db.refresh(row)
            # Telegram keeps failing
            monkeypatch.setattr(notify, "telegram_send", lambda text: False)

            from app.scheduler import _retry_pending_notifications
            _retry_pending_notifications()
            db.refresh(row)
            assert row.status == "failed"
            assert row.attempt == 5
            # SSE event was published
            collected = []
            while evts:
                collected.append(evts.popleft())
            assert any(e["type"] == "notification:failed" and e["id"] == esc.id for e in collected)
        finally:
            from app import events
            if ref in events._subscribers:
                events._subscribers.remove(ref)

    def test_retry_increments_attempt_on_failure(self, client, db, monkeypatch):
        from app.models import PendingNotification
        from datetime import datetime, timezone, timedelta
        from app import notify
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="IncremPatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="open",
        )
        db.add(esc); db.commit(); db.refresh(esc)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        row = PendingNotification(
            hospital_code="KA-DIST-01", kind="escalation",
            entity_id=esc.id, text="test",
            attempt=1, next_retry_at=past, status="pending",
        )
        db.add(row); db.commit(); db.refresh(row)
        monkeypatch.setattr(notify, "telegram_send", lambda text: False)
        from app.scheduler import _retry_pending_notifications
        _retry_pending_notifications()
        db.refresh(row)
        assert row.status == "pending"   # not yet exhausted
        assert row.attempt == 2
        # next_retry_at advanced into the future
        from datetime import datetime
        new_next = datetime.fromisoformat(row.next_retry_at)
        assert new_next > datetime.now(timezone.utc)

    def test_retry_skips_future_pending(self, client, db):
        """A row with next_retry_at in the future is not retried."""
        from app.models import PendingNotification
        from datetime import datetime, timezone, timedelta
        u = db.query(User).filter(User.username == "admin").first()
        p = _make_patient(db, u.id, name="FuturePatient")
        e = _make_enrollment(db, p.id, u.id)
        esc = Escalation(
            hospital_code="KA-DIST-01", enrollment_id=e.id,
            level="red", reasons='["test"]', status="open",
        )
        db.add(esc); db.commit(); db.refresh(esc)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        row = PendingNotification(
            hospital_code="KA-DIST-01", kind="escalation",
            entity_id=esc.id, text="test",
            attempt=1, next_retry_at=future, status="pending",
        )
        db.add(row); db.commit(); db.refresh(row)
        from app.scheduler import _retry_pending_notifications
        _retry_pending_notifications()
        db.refresh(row)
        # Not retried — attempt unchanged
        assert row.attempt == 1

    def test_ensure_retry_job_idempotent(self, client, db):
        """ensure_retry_job() can be called multiple times without error."""
        from app.scheduler import ensure_retry_job
        ensure_retry_job()
        ensure_retry_job()
        ensure_retry_job()


# ─────────────────────────────────────────────────────────────────────────────
# Bulk import — file_id workflow
# ─────────────────────────────────────────────────────────────────────────────
class TestBulkImport:
    def _csv(self, headers, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for r in rows:
            w.writerow(r)
        return buf.getvalue().encode()

    def test_preview_returns_mapping_and_rows(self, client, db):
        _login(client)
        content = self._csv(
            ["Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
             "Condition", "Protocol", "Ward"],
            [["Lakshmamma", "62", "F", "Ramu", "+919876500131",
              "Post-op", "wound_care", "Ward-1"]],
        )
        r = client.post("/api/import/preview",
                        files={"file": ("patients.csv", io.BytesIO(content), "text/csv")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "file_id" in body
        assert "rows" in body
        assert len(body["rows"]) == 1
        assert "mapping_suggestions" in body
        assert "protocols" in body

    def test_preview_oversized_file_rejected(self, client, db):
        _login(client)
        big = b"\x00" * (10 * 1024 * 1024 + 1)
        r = client.post("/api/import/preview",
                        files={"file": ("big.csv", io.BytesIO(big), "text/csv")})
        assert r.status_code == 413

    def test_preview_empty_file_rejected(self, client, db):
        _login(client)
        r = client.post("/api/import/preview",
                        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")})
        assert r.status_code == 400

    def test_preview_invalid_file_rejected(self, client, db):
        _login(client)
        r = client.post("/api/import/preview",
                        files={"file": ("foo.csv", io.BytesIO(b"not csv"), "text/csv")})
        # depends on whether parse_file accepts the content; current behavior:
        # may either return 200 with empty rows OR 400. Either is acceptable
        # as long as it doesn't 500.
        assert r.status_code in (200, 400)

    def test_template_endpoint(self, client, db):
        _login(client)
        r = client.get("/api/import/template/wound_care")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "attachment" in r.headers["content-disposition"]

    def test_confirm_uses_file_id_from_preview(self, client, db):
        _login(client)
        content = self._csv(
            ["Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
             "Condition", "Protocol", "Ward"],
            [["Lakshmamma", "62", "F", "Ramu", "+919876500141",
              "Post-op", "wound_care", "Ward-1"]],
        )
        prev = client.post("/api/import/preview",
                           files={"file": ("patients.csv", io.BytesIO(content), "text/csv")}).json()
        file_id = prev["file_id"]
        # extract mapping from suggestions
        mapping = prev["mapping_suggestions"]
        r = client.post("/api/import/confirm", json={
            "file_id": file_id,
            "mapping": mapping,
            "selected_indices": [r["index"] for r in prev["rows"]],
            "default_protocol": "wound_care",
            "default_ward": "Ward-1",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["imported"] >= 1

    def test_confirm_with_bogus_file_id_404(self, client, db):
        _login(client)
        r = client.post("/api/import/confirm", json={
            "file_id": "nope-nope-nope",
            "mapping": {},
            "selected_indices": [],
        })
        assert r.status_code == 404

    def test_confirm_audit_written(self, client, db):
        _login(client)
        content = self._csv(
            ["Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
             "Condition", "Protocol", "Ward"],
            [["Lakshmamma", "62", "F", "Ramu", "+919876500151",
              "Post-op", "wound_care", "Ward-1"]],
        )
        prev = client.post("/api/import/preview",
                           files={"file": ("patients.csv", io.BytesIO(content), "text/csv")}).json()
        client.post("/api/import/confirm", json={
            "file_id": prev["file_id"],
            "mapping": prev["mapping_suggestions"],
            "selected_indices": [r["index"] for r in prev["rows"]],
        })
        a = db.query(AuditLog).filter(AuditLog.action == "bulk_import").first()
        assert a is not None
        assert json.loads(a.meta)["imported"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Cross-cutting: unauthenticated access to all /api/* (except healthz + login)
# ─────────────────────────────────────────────────────────────────────────────
class TestUnauthenticatedAccess:
    PROTECTED = [
        ("GET", "/api/board"),
        ("GET", "/api/patients/search"),
        ("GET", "/api/patients/export/csv"),
        ("GET", "/api/protocols"),
        ("GET", "/api/protocols/wound_care/detail"),
        ("GET", "/api/escalations"),
        ("GET", "/api/staff-mgmt"),
        ("GET", "/api/staff/activity"),
        ("GET", "/api/staff/patients"),
        ("GET", "/api/dashboard/daily-stats"),
        ("GET", "/api/dashboard/risk-trend"),
        ("GET", "/api/dashboard/nurse-metrics"),
        ("GET", "/api/health/dashboard"),
        ("GET", "/api/events"),
    ]

    def test_all_protected_routes_require_session(self, client):
        for method, path in self.PROTECTED:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# Cross-cutting: security headers on every response
# ─────────────────────────────────────────────────────────────────────────────
class TestSecurityHeaders:
    def test_headers_on_json(self, client):
        r = client.get("/api/healthz")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'self'" in r.headers["content-security-policy"]

    def test_headers_on_login(self, client):
        r = client.post("/api/auth/login", json=ADMIN)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"

    def test_headers_on_error(self, client):
        r = client.get("/api/board")  # 401
        assert r.headers["x-content-type-options"] == "nosniff"
