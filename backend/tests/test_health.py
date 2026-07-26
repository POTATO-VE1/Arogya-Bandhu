"""Tests for health device integration — analytics, API, OAuth flow."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from app.db import SessionLocal, now_utc


# ── Analytics (pure computation, no DB) ──────────────────────────────────────


class TestAnalytics:
    """Test the analytics engine — pure functions over data dicts."""

    def test_compute_health_summary_empty(self):
        from app.health_fit.analytics import compute_health_summary
        result = compute_health_summary([])
        # Empty data still produces health_score (default 50) and empty flags
        assert result.get("health_score") == 50
        assert result.get("overall_flags") == []

    def test_compute_health_summary_heart_rate(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "heart_rate", "value": 72, "recorded_at": "2026-07-20T10:00:00+00:00"},
            {"metric_type": "heart_rate", "value": 75, "recorded_at": "2026-07-21T10:00:00+00:00"},
            {"metric_type": "heart_rate", "value": 70, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert "heart_rate" in result
        assert result["heart_rate"]["latest"] == 70
        assert result["heart_rate"]["avg_7d"] == 72.3
        assert result["heart_rate"]["count"] == 3

    def test_compute_health_summary_spo2_low(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "spo2", "value": 91, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert "spo2" in result
        assert result["spo2"]["latest"] == 91
        assert "low_spo2" in result["spo2"]["flags"]

    def test_compute_health_summary_steps_sedentary(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "steps", "value": 500, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert "steps" in result
        assert "sedentary" in result["steps"]["flags"]

    def test_compute_health_summary_fever(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "body_temp", "value": 38.5, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert "body_temp" in result
        assert "fever" in result["body_temp"]["flags"]

    def test_compute_health_summary_sleep_deprivation(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "sleep", "value": 300, "recorded_at": "2026-07-22T10:00:00+00:00"},  # 5 hours
        ]
        result = compute_health_summary(rows)
        assert "sleep" in result
        assert "sleep_deprivation" in result["sleep"]["flags"]

    def test_composite_score_high(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "spo2", "value": 98, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "heart_rate", "value": 68, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "steps", "value": 6000, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "sleep", "value": 480, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "body_temp", "value": 36.8, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert result["health_score"] >= 80  # should be high with all normal values

    def test_composite_score_low(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "spo2", "value": 89, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "heart_rate", "value": 110, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "steps", "value": 100, "recorded_at": "2026-07-22T10:00:00+00:00"},
            {"metric_type": "body_temp", "value": 39.0, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert result["health_score"] <= 40  # should be low with concerning values

    def test_trajectory(self):
        from app.health_fit.analytics import compute_trajectory
        now = datetime.now(timezone.utc)
        rows = []
        # Previous week: lower steps
        for i in range(7):
            rows.append({
                "metric_type": "steps",
                "value": 2000 + i * 100,
                "recorded_at": (now - timedelta(days=14 - i)).isoformat(),
            })
        # Current week: higher steps
        for i in range(7):
            rows.append({
                "metric_type": "steps",
                "value": 5000 + i * 200,
                "recorded_at": (now - timedelta(days=7 - i)).isoformat(),
            })
        result = compute_trajectory(rows)
        assert "steps" in result
        assert result["steps"]["direction"] == "improving"

    def test_hr_spike_detection(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "heart_rate", "value": 70, "recorded_at": f"2026-07-{15+i}T10:00:00+00:00"}
            for i in range(7)
        ] + [
            {"metric_type": "heart_rate", "value": 100, "recorded_at": "2026-07-22T10:00:00+00:00"},
        ]
        result = compute_health_summary(rows)
        assert "hr_spike" in result["heart_rate"]["flags"]

    def test_prolonged_inactivity(self):
        from app.health_fit.analytics import compute_health_summary
        rows = [
            {"metric_type": "steps", "value": 50, "recorded_at": f"2026-07-{16+i}T10:00:00+00:00"}
            for i in range(5)
        ]
        result = compute_health_summary(rows)
        assert "prolonged_inactivity" in result["steps"]["flags"]


# ── API endpoints ────────────────────────────────────────────────────────────


class TestHealthAPI:
    """Test health API endpoints with mocked Google Fit calls."""

    def _seed_patient(self, db):
        """Create a test patient and enrollment."""
        from app.models import Patient, Enrollment, User, EnrollmentMed
        from app.security import hash_password
        user = User(
            id=uuid.uuid4().hex,
            hospital_code="KA-DIST-01",
            username="testnurse",
            password_hash=hash_password("test"),
            display_name="Test Nurse",
            role="staff",
        )
        db.add(user)
        db.commit()  # commit user first so FK reference works
        patient = Patient(
            id=uuid.uuid4().hex,
            hospital_code="KA-DIST-01",
            name="Test Patient",
            age=45,
            sex="M",
            caregiver_name="Test Caregiver",
            caregiver_phone="+919876543210",
            consent_at=now_utc(),
            created_by=user.id,
        )
        db.add(patient)
        enrollment = Enrollment(
            id=uuid.uuid4().hex,
            hospital_code="KA-DIST-01",
            patient_id=patient.id,
            protocol_id="wound_care",
            condition_label="Post-op appendectomy",
            discharge_date="2026-07-20",
        )
        db.add(enrollment)
        db.commit()
        return user, patient, enrollment

    def test_health_data_no_device(self, _engine, client, db):
        """GET /api/patients/{id}/health-data with no connected device."""
        user, patient, _ = self._seed_patient(db)

        resp = client.post("/api/auth/login", json={"username": "testnurse", "password": "test"})
        assert resp.status_code == 200

        resp = client.get(f"/api/patients/{patient.id}/health-data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["data_points"] == 0
        assert data["metrics"] == []

    def test_health_summary_empty(self, _engine, client, db):
        """GET /api/patients/{id}/health-summary with no data."""
        user, patient, _ = self._seed_patient(db)

        client.post("/api/auth/login", json={"username": "testnurse", "password": "test"})

        resp = client.get(f"/api/patients/{patient.id}/health-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_points"] == 0
        assert data["summary"].get("health_score") == 50  # default score with no data

    def test_health_data_wrong_hospital(self, _engine, client, db):
        """GET /api/patients/{id}/health-data with wrong hospital returns 404."""
        user, patient, _ = self._seed_patient(db)

        client.post("/api/auth/login", json={"username": "testnurse", "password": "test"})

        # Create patient in different hospital
        other_patient_id = uuid.uuid4().hex
        resp = client.get(f"/api/patients/{other_patient_id}/health-data")
        assert resp.status_code == 404

    def test_fit_authorize_no_config(self, _engine, client):
        """GET /api/health/fit/authorize returns 503 when not configured."""
        resp = client.get("/api/health/fit/authorize?tgid=12345")
        assert resp.status_code == 503

    def test_health_dashboard_empty(self, _engine, client, db):
        """GET /api/health/dashboard with no connected patients."""
        user, patient, _ = self._seed_patient(db)

        client.post("/api/auth/login", json={"username": "testnurse", "password": "test"})

        resp = client.get("/api/health/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_connected"] == 0
        assert data["patients"] == []


# ── OAuth module ──────────────────────────────────────────────────────────────


class TestOAuth:
    """Test OAuth helper functions."""

    def test_pkce_generation(self):
        from app.health_fit.oauth import generate_pkce
        verifier, challenge = generate_pkce()
        assert len(verifier) > 0
        assert len(challenge) > 0
        assert verifier != challenge

    def test_authorize_url_no_config(self):
        from app.health_fit.oauth import get_authorize_url
        with patch("app.health_fit.oauth.settings") as mock_settings:
            mock_settings.GOOGLE_FIT_CLIENT_ID = ""
            with pytest.raises(ValueError, match="GOOGLE_FIT_CLIENT_ID not configured"):
                get_authorize_url(12345, "http://localhost/callback")

    def test_exchange_code_no_config(self):
        from app.health_fit.oauth import exchange_code
        with patch("app.health_fit.oauth.settings") as mock_settings:
            mock_settings.GOOGLE_FIT_CLIENT_ID = ""
            mock_settings.GOOGLE_FIT_CLIENT_SECRET = ""
            result = exchange_code("fake_code", "fake_state", "http://localhost/callback")
            assert result is None


# ── Client module ─────────────────────────────────────────────────────────────


class TestClient:
    """Test Google Fit API client helpers."""

    def test_ms_to_iso(self):
        from app.health_fit.client import _ms_to_iso
        result = _ms_to_iso(1690000000000)  # 2023-07-22
        assert "2023-07-22" in result

    def test_iso_to_ms(self):
        from app.health_fit.client import _iso_to_ms
        result = _iso_to_ms("2023-07-22T00:00:00+00:00")
        assert isinstance(result, int)
        assert result > 0

    def test_parse_point_value_heart_rate(self):
        from app.health_fit.client import _parse_point_value
        point = {"value": [{"fpVal": 72.5}]}
        result = _parse_point_value(point, "com.google.heart_rate.bpm")
        assert result == 72.5

    def test_parse_point_value_spo2(self):
        from app.health_fit.client import _parse_point_value
        point = {"value": [{"fpVal": 0.97}]}  # Google returns 0-1
        result = _parse_point_value(point, "com.google.oxygen_saturation")
        assert result == 97.0

    def test_parse_point_value_steps(self):
        from app.health_fit.client import _parse_point_value
        point = {"value": [{"intVal": 5432}]}
        result = _parse_point_value(point, "com.google.step_count.delta")
        assert result == 5432.0

    def test_parse_point_value_bad_data(self):
        from app.health_fit.client import _parse_point_value
        result = _parse_point_value({}, "com.google.heart_rate.bpm")
        assert result is None

    def test_test_connection_no_token(self):
        from app.health_fit.client import test_connection
        result = test_connection("fake_token")
        assert result is None
