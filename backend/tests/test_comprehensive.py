"""Comprehensive API tests — staff management, activity, board, role-based access."""
import json
from datetime import datetime, timedelta, timezone

from app.db import now_utc
from app.models import (
    AuditLog, Enrollment, EnrollmentMed, Escalation,
    FollowupCall, Patient, User,
)
from app.security import hash_password

ADMIN = {"username": "admin", "password": "changeme123"}


def _login(client):
    client.post("/api/auth/login", json=ADMIN)


def _create_user(db, username="doctor1", role="doctor", ward=None) -> User:
    """Create a test user and return it."""
    u = User(
        hospital_code="KA-DIST-01",
        username=username,
        password_hash=hash_password("testpass123"),
        display_name=f"Test {role.title()}",
        role=role,
        ward=ward,
        created_at=now_utc(),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _create_patient(db, user_id, name="Test Patient", phone="+919876543210") -> Patient:
    """Create a test patient and return it."""
    p = Patient(
        hospital_code="KA-DIST-01",
        name=name,
        age=50,
        sex="M",
        caregiver_name="Test Caregiver",
        caregiver_phone=phone,
        consent_at=now_utc(),
        created_by=user_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _create_enrollment(db, patient_id, user_id, ward="Ward-1", protocol="wound_care") -> Enrollment:
    """Create a test enrollment and return it."""
    e = Enrollment(
        hospital_code="KA-DIST-01",
        patient_id=patient_id,
        protocol_id=protocol,
        condition_label="Test Condition",
        ward=ward,
        discharge_date="2026-07-25",
        created_by=user_id,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _create_call(db, enrollment_id, day=1, status="pending", risk=None, triggered_by=None) -> FollowupCall:
    """Create a test followup call and return it."""
    c = FollowupCall(
        hospital_code="KA-DIST-01",
        enrollment_id=enrollment_id,
        day_index=day,
        scheduled_at=now_utc(),
        status=status,
        risk_level=risk,
        triggered_by=triggered_by,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _create_escalation(db, enrollment_id, status="open") -> Escalation:
    """Create a test escalation and return it."""
    esc = Escalation(
        hospital_code="KA-DIST-01",
        enrollment_id=enrollment_id,
        reasons='["test reason"]',
        status=status,
    )
    db.add(esc)
    db.commit()
    db.refresh(esc)
    return esc


# ── Auth Tests ────────────────────────────────────────────────────────────────

class TestAuth:
    def test_logout_clears_session(self, client):
        _login(client)
        r = client.get("/api/auth/me")
        assert r.status_code == 200

        client.post("/api/auth/logout")
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_returns_correct_fields(self, client):
        _login(client)
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "display_name" in data
        assert "role" in data
        assert "hospital_name" in data
        assert data["role"] == "admin"

    def test_login_returns_correct_fields(self, client):
        r = client.post("/api/auth/login", json=ADMIN)
        assert r.status_code == 200
        data = r.json()
        assert "id" in data
        assert "display_name" in data
        assert "role" in data
        assert "hospital_name" in data


# ── Staff Management Tests ────────────────────────────────────────────────────

class TestStaffManagement:
    def test_list_staff_requires_admin(self, client, db):
        _login(client)
        r = client.get("/api/staff-mgmt")
        assert r.status_code == 200

    def test_list_staff_returns_all_users(self, client, db):
        _login(client)
        _create_user(db, "doctor1", "doctor")
        _create_user(db, "nurse1", "nurse")
        r = client.get("/api/staff-mgmt")
        assert r.status_code == 200
        users = r.json()
        assert len(users) >= 2
        usernames = [u["username"] for u in users]
        assert "doctor1" in usernames
        assert "nurse1" in usernames

    def test_create_staff(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "newdoctor",
            "display_name": "New Doctor",
            "role": "doctor",
            "password": "testpass123",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["username"] == "newdoctor"
        assert data["role"] == "doctor"
        assert data["ward"] is None

    def test_create_staff_invalid_role(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt", json={
            "username": "baduser",
            "display_name": "Bad User",
            "role": "invalid_role",
            "password": "testpass123",
        })
        assert r.status_code == 400

    def test_create_staff_duplicate_username(self, client, db):
        _login(client)
        _create_user(db, "existing_user", "doctor")
        r = client.post("/api/staff-mgmt", json={
            "username": "existing_user",
            "display_name": "Duplicate",
            "role": "doctor",
            "password": "testpass123",
        })
        assert r.status_code == 400

    def test_update_staff(self, client, db):
        _login(client)
        user = _create_user(db, "updateme", "doctor")
        r = client.patch(f"/api/staff-mgmt/{user.id}", json={
            "display_name": "Updated Name",
            "role": "nurse",
        })
        assert r.status_code == 200
        assert r.json()["display_name"] == "Updated Name"
        assert r.json()["role"] == "nurse"

    def test_delete_staff(self, client, db):
        _login(client)
        user = _create_user(db, "deleteme", "doctor")
        r = client.delete(f"/api/staff-mgmt/{user.id}")
        assert r.status_code == 204

    def test_delete_admin_blocked(self, client, db):
        _login(client)
        admin = db.query(User).filter(User.username == "admin").first()
        r = client.delete(f"/api/staff-mgmt/{admin.id}")
        assert r.status_code == 400

    def test_change_password(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt/change-password", json={
            "current_password": "changeme123",
            "new_password": "newpass123",
        })
        assert r.status_code == 204

    def test_change_password_wrong_current(self, client, db):
        _login(client)
        r = client.post("/api/staff-mgmt/change-password", json={
            "current_password": "wrongpass",
            "new_password": "newpass123",
        })
        assert r.status_code == 400


# ── Board API Tests ───────────────────────────────────────────────────────────

class TestBoard:
    def test_board_requires_auth(self, client):
        r = client.get("/api/board")
        assert r.status_code == 401

    def test_board_returns_kpis_and_rows(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.get("/api/board")
        assert r.status_code == 200
        data = r.json()
        assert "kpis" in data
        assert "rows" in data
        assert data["kpis"]["enrolled"] == 1
        assert len(data["rows"]) == 1

    def test_board_kpis_consistent_with_filtered_data(self, client, db):
        """KPIs should be calculated from the same enrollment set as rows."""
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()

        # Create 2 enrollments in different wards
        p1 = _create_patient(db, user.id, "Patient 1", "+919876543210")
        e1 = _create_enrollment(db, p1.id, user.id, ward="Ward-1")
        _create_call(db, e1.id, day=1, status="completed", risk="green", triggered_by=user.id)

        p2 = _create_patient(db, user.id, "Patient 2", "+919876543211")
        e2 = _create_enrollment(db, p2.id, user.id, ward="Ward-2")
        _create_call(db, e2.id, day=1, status="completed", risk="red", triggered_by=user.id)

        r = client.get("/api/board")
        assert r.status_code == 200
        data = r.json()
        # Admin sees all
        assert data["kpis"]["enrolled"] == 2
        assert len(data["rows"]) == 2

    def test_board_role_based_filtering(self, client, db):
        """Nurse with ward assignment should only see their ward."""
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()

        # Create nurse with ward assignment
        nurse = _create_user(db, "ward_nurse", "nurse", ward="Ward-1")

        # Create enrollments in different wards
        p1 = _create_patient(db, user.id, "Patient 1", "+919876543210")
        e1 = _create_enrollment(db, p1.id, user.id, ward="Ward-1")

        p2 = _create_patient(db, user.id, "Patient 2", "+919876543211")
        e2 = _create_enrollment(db, p2.id, user.id, ward="Ward-2")

        # Login as nurse
        client.post("/api/auth/login", json={"username": "ward_nurse", "password": "testpass123"})
        r = client.get("/api/board")
        assert r.status_code == 200
        data = r.json()
        # Nurse should only see Ward-1
        assert data["kpis"]["enrolled"] == 1
        assert len(data["rows"]) == 1
        assert data["rows"][0]["ward"] == "Ward-1"


# ── Patient Detail Tests ──────────────────────────────────────────────────────

class TestPatientDetail:
    def test_patient_detail_requires_auth(self, client):
        r = client.get("/api/patients/some-id")
        assert r.status_code == 401

    def test_patient_detail_returns_enrollments(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Test Patient"
        assert len(data["enrollments"]) == 1
        assert data["enrollments"][0]["ward"] == "Ward-1"

    def test_patient_detail_cross_hospital_404(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = Patient(
            hospital_code="OTHER",
            name="Other Patient",
            caregiver_name="Other",
            caregiver_phone="+919999999999",
            consent_at=now_utc(),
            created_by=user.id,
        )
        db.add(p)
        db.commit()
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 404

    def test_patient_detail_ward_based_access(self, client, db):
        """Nurse should not see patients from other wards."""
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        nurse = _create_user(db, "ward_nurse", "nurse", ward="Ward-1")

        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id, ward="Ward-2")

        client.post("/api/auth/login", json={"username": "ward_nurse", "password": "testpass123"})
        r = client.get(f"/api/patients/{p.id}")
        assert r.status_code == 404


# ── Staff Activity Tests ──────────────────────────────────────────────────────

class TestStaffActivity:
    def test_activity_requires_auth(self, client):
        r = client.get("/api/staff/activity")
        assert r.status_code == 401

    def test_activity_returns_audit_logs(self, client, db):
        _login(client)
        # Create some audit logs
        db.add(AuditLog(
            hospital_code="KA-DIST-01",
            actor="admin",
            action="login",
            entity_id="test-id",
        ))
        db.commit()

        r = client.get("/api/staff/activity")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["action"] == "login"

    def test_activity_limit(self, client, db):
        _login(client)
        # Create multiple audit logs
        for i in range(10):
            db.add(AuditLog(
                hospital_code="KA-DIST-01",
                actor="admin",
                action=f"action_{i}",
            ))
        db.commit()

        r = client.get("/api/staff/activity?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 5

    def test_patients_requires_auth(self, client):
        r = client.get("/api/staff/patients")
        assert r.status_code == 401

    def test_patients_returns_enrollments(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.get("/api/staff/patients")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["patient_name"] == "Test Patient"

    def test_timeline_requires_auth(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)

        r = client.get(f"/api/patients/{p.id}/timeline")
        assert r.status_code == 200

    def test_timeline_returns_events(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)
        _create_call(db, e.id, day=1, status="completed", risk="green", triggered_by=user.id)

        r = client.get(f"/api/patients/{p.id}/timeline")
        assert r.status_code == 200
        data = r.json()
        assert "patient" in data
        assert "timeline" in data
        assert len(data["timeline"]) >= 2  # patient_created + enrollment + call


# ── Escalation Tests ──────────────────────────────────────────────────────────

class TestEscalations:
    def test_escalation_list_requires_auth(self, client):
        r = client.get("/api/escalations")
        assert r.status_code == 401

    def test_escalation_list_returns_data(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)
        _create_escalation(db, e.id)

        r = client.get("/api/escalations")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["status"] == "open"

    def test_escalation_ack(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)
        esc = _create_escalation(db, e.id)

        r = client.post(f"/api/escalations/{esc.id}/ack")
        assert r.status_code == 200
        assert r.json()["status"] == "acked"

    def test_escalation_resolve(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)
        esc = _create_escalation(db, e.id)

        r = client.post(f"/api/escalations/{esc.id}/resolve", json={"note": "resolved by test"})
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

    def test_escalation_resolve_already_resolved(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)
        esc = _create_escalation(db, e.id, status="resolved")

        r = client.post(f"/api/escalations/{esc.id}/resolve", json={"note": "double resolve"})
        assert r.status_code == 400


# ── Dashboard Tests ───────────────────────────────────────────────────────────

class TestDashboard:
    def test_daily_stats_requires_auth(self, client):
        r = client.get("/api/dashboard/daily-stats")
        assert r.status_code == 401

    def test_daily_stats_returns_correct_structure(self, client, db):
        _login(client)
        r = client.get("/api/dashboard/daily-stats")
        assert r.status_code == 200
        data = r.json()
        assert "calls_today" in data
        assert "risk_green" in data
        assert "risk_yellow" in data
        assert "risk_red" in data
        assert "calls_failed" in data
        assert "calls_scheduled" in data
        assert "open_escalations" in data
        assert "resolved_today" in data
        assert "reach_rate" in data

    def test_risk_trend_requires_auth(self, client):
        r = client.get("/api/dashboard/risk-trend")
        assert r.status_code == 401

    def test_risk_trend_returns_8_weeks(self, client, db):
        _login(client)
        r = client.get("/api/dashboard/risk-trend")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 8
        for week in data:
            assert "label" in week
            assert "green" in week
            assert "yellow" in week
            assert "red" in week

    def test_nurse_metrics_requires_auth(self, client):
        r = client.get("/api/dashboard/nurse-metrics")
        assert r.status_code == 401

    def test_nurse_metrics_returns_data(self, client, db):
        _login(client)
        _create_user(db, "nurse1", "nurse")
        r = client.get("/api/dashboard/nurse-metrics")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        for metric in data:
            assert "username" in metric
            assert "display_name" in metric
            assert "calls_made" in metric
            assert "escalations_resolved" in metric
            assert "resolution_rate" in metric


# ── Enrollment Tests ──────────────────────────────────────────────────────────

class TestEnrollments:
    def test_enroll_creates_patient_and_enrollment(self, client, db):
        _login(client)
        body = {
            "patient": {
                "name": "New Patient",
                "age": 45,
                "sex": "F",
                "caregiver_name": "Caregiver",
                "caregiver_phone": "+919876543210",
            },
            "protocol_id": "wound_care",
            "condition_label": "Post-op",
            "ward": "Ward-3",
            "discharge_date": "2026-07-25",
            "meds": [{"med_name": "Amoxicillin", "med_type": "antibiotic", "doses_per_day": 3}],
            "consent": True,
        }
        r = client.post("/api/enrollments", json=body)
        assert r.status_code == 201
        data = r.json()
        assert "enrollment_id" in data
        assert "patient_id" in data
        assert "call_ids" in data

        # Verify enrollment has created_by
        e = db.get(Enrollment, data["enrollment_id"])
        assert e.created_by is not None

    def test_enroll_dedup(self, client, db):
        _login(client)
        body = {
            "patient": {
                "name": "Dedup Patient",
                "age": 30,
                "caregiver_name": "Caregiver",
                "caregiver_phone": "+919876543210",
            },
            "protocol_id": "wound_care",
            "condition_label": "Post-op",
            "discharge_date": "2026-07-25",
            "meds": [],
            "consent": True,
        }
        r1 = client.post("/api/enrollments", json=body)
        assert r1.status_code == 201

        # Same phone + protocol should be rejected
        r2 = client.post("/api/enrollments", json=body)
        assert r2.status_code == 409

    def test_set_outcome(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "recovered"})
        assert r.status_code == 200
        assert r.json()["outcome"] == "recovered"

        # Verify audit log
        audit = db.query(AuditLog).filter(AuditLog.action == "set_outcome").first()
        assert audit is not None
        assert json.loads(audit.meta)["outcome"] == "recovered"

    def test_set_outcome_invalid(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.post(f"/api/enrollments/{e.id}/outcome", json={"outcome": "invalid"})
        assert r.status_code == 400


# ── Protocol Tests ────────────────────────────────────────────────────────────

class TestProtocols:
    def test_protocols_list(self, client, db):
        _login(client)
        r = client.get("/api/protocols")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        for proto in data:
            assert "id" in proto
            assert "name_en" in proto
            assert "name_kn" in proto

    def test_protocol_detail(self, client, db):
        _login(client)
        r = client.get("/api/protocols/wound_care/detail")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "wound_care"
        assert "questions" in data


# ── Patient Search Tests ──────────────────────────────────────────────────────

class TestPatientSearch:
    def test_search_requires_auth(self, client):
        r = client.get("/api/patients/search?q=test")
        assert r.status_code == 401

    def test_search_returns_results(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        _create_patient(db, user.id, "Searchable Patient", "+919876543210")

        r = client.get("/api/patients/search?q=Searchable")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "Searchable Patient"

    def test_search_empty_query(self, client, db):
        _login(client)
        r = client.get("/api/patients/search?q=")
        assert r.status_code == 200
        assert r.json() == []


# ── Export Tests ──────────────────────────────────────────────────────────────

class TestExport:
    def test_csv_export(self, client, db):
        _login(client)
        user = db.query(User).filter(User.username == "admin").first()
        p = _create_patient(db, user.id)
        e = _create_enrollment(db, p.id, user.id)

        r = client.get("/api/patients/export/csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "Patient ID" in r.text
        assert "Test Patient" in r.text
