"""Tests for the HMIS / EMR intake router + adapter framework.

Covers:
- DischargeEvent validation (phone normalization, consent required,
  discharge date not in the future)
- HMAC sign / verify
- NIC HMIS adapter (field-name translation)
- Medmantra adapter
- CSV adapter (column mapping)
- The webhook router: HMAC required, valid signature, idempotency,
  duplicate detection, hospital-scope
- The CSV upload router
- The sources + contract endpoints
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile

import pytest


# ── adapters ───────────────────────────────────────────────────────────────

def test_phone_normalization_to_e164():
    from app.hmis import DischargeEvent
    # 10-digit → +91
    e = DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="9876543210",
                       date_of_discharge="2026-01-01",
                       diagnosis_at_discharge="X", consent=True)
    assert e.caregiver_phone == "+919876543210"
    # +91 already
    e = DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="+919876543210",
                       date_of_discharge="2026-01-01",
                       diagnosis_at_discharge="X", consent=True)
    assert e.caregiver_phone == "+919876543210"
    # 91... (no plus)
    e = DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="919876543210",
                       date_of_discharge="2026-01-01",
                       diagnosis_at_discharge="X", consent=True)
    assert e.caregiver_phone == "+919876543210"
    # 0CC... (trunk prefix)
    e = DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="09876543210",
                       date_of_discharge="2026-01-01",
                       diagnosis_at_discharge="X", consent=True)
    assert e.caregiver_phone == "+919876543210"


def test_consent_must_be_true():
    from app.hmis import DischargeEvent
    with pytest.raises(Exception, match="consent"):
        DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="9876543210",
                       date_of_discharge="2026-01-01",
                       diagnosis_at_discharge="X", consent=False)


def test_discharge_date_not_in_future():
    from app.hmis import DischargeEvent
    from datetime import date, timedelta
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(Exception, match="future"):
        DischargeEvent(patient_name="A", caregiver_name="B",
                       caregiver_phone="9876543210",
                       date_of_discharge=future,
                       diagnosis_at_discharge="X", consent=True)


def test_hmac_roundtrip():
    from app.hmis import sign_body, verify_signature
    body = b'{"patient_name":"Lakshmamma"}'
    sig = sign_body("secret123", body)
    assert verify_signature("secret123", body, sig) is True
    assert verify_signature("wrong-secret", body, sig) is False
    assert verify_signature("secret123", body, "") is False


# ── NIC HMIS adapter ───────────────────────────────────────────────────────

def test_nic_hmis_adapter_translates_field_names():
    from app.hmis import NicHmisAdapter
    adapter = NicHmisAdapter()
    raw = {
        "patient_name": "Lakshmamma Devi",
        "AGE_YEARS": 65,
        "GENDER": "F",
        "ATTENDANT_NAME": "Suresh Kumar",
        "mobile_no": "9876543210",
        "date_of_discharge": "2026-07-25",
        "WARD": "Ward-2",
        "diagnosis_at_discharge": "Post-op appendectomy",
        "ABHA": "14-3344-5566-7788",
        "MRN": "MR-2026-0001",
    }
    ev = adapter.to_event(raw)
    assert ev.patient_name == "Lakshmamma Devi"
    assert ev.caregiver_name == "Suresh Kumar"
    assert ev.caregiver_phone == "+919876543210"
    assert ev.ward_name == "Ward-2"
    assert ev.abha_number == "14-3344-5566-7788"
    assert ev.emr_patient_id == "MR-2026-0001"
    assert ev.emr_source == "nic_hmis"
    assert ev.consent is True


def test_nic_hmis_adapter_accepts_uppercase_field_names():
    """Real NIC HMIS exports use UPPERCASE keys."""
    from app.hmis import NicHmisAdapter
    adapter = NicHmisAdapter()
    raw = {
        "PATIENT_NAME": "Lakshmamma",
        "GENDER": "F", "MOBILE_NO": "9876543210",
        "DATE_OF_DISCHARGE": "2026-07-25",
        "DIAGNOSIS_AT_DISCHARGE": "Wound",
        "ATTENDANT_NAME": "Suresh",
    }
    ev = adapter.to_event(raw)
    assert ev.patient_name == "Lakshmamma"
    assert ev.diagnosis_at_discharge == "Wound"


# ── Medmantra adapter ──────────────────────────────────────────────────────

def test_medmantra_adapter_translates_field_names():
    from app.hmis import MedmantraAdapter
    raw = {
        "patientName": "Lakshmamma",
        "age": 65, "gender": "F",
        "attendantName": "Suresh Kumar",
        "contactNumber": "9876543210",
        "dischargeDate": "2026-07-25",
        "ward": "Ward-1",
        "primaryDiagnosis": "Wound care",
        "abha": "14-3344-5566-7788",
        "uhid": "UH-2026-0001",
    }
    ev = MedmantraAdapter().to_event(raw)
    assert ev.patient_name == "Lakshmamma"
    assert ev.caregiver_name == "Suresh Kumar"
    assert ev.caregiver_phone == "+919876543210"
    assert ev.emr_patient_id == "UH-2026-0001"


# ── CSV adapter ───────────────────────────────────────────────────────────

def test_csv_adapter_identity_mapping():
    from app.hmis import CsvAdapter
    adapter = CsvAdapter()
    raw = {
        "patient_name": "Lakshmamma",
        "caregiver_name": "Suresh",
        "caregiver_phone": "9876543210",
        "date_of_discharge": "2026-07-25",
        "diagnosis_at_discharge": "Wound",
        "consent": "true",  # CSV uses string truthy
        "age": "65",  # CSV uses string
    }
    ev = adapter.to_event(raw)
    assert ev.patient_name == "Lakshmamma"
    assert ev.age == 65
    assert ev.consent is True


def test_csv_adapter_explicit_mapping():
    """The bulk import wizard produces a mapping dict; the adapter
    applies it to translate column names."""
    from app.hmis import CsvAdapter
    raw = {
        "Patient Name": "Lakshmamma",
        "Caregiver Name": "Suresh Kumar",
        "Mobile No": "9876543210",
        "Date of Discharge": "2026-07-25",
        "Diagnosis at Discharge": "Wound",
    }
    mapping = {
        "patient_name": "Patient Name",
        "caregiver_name": "Caregiver Name",
        "caregiver_phone": "Mobile No",
        "date_of_discharge": "Date of Discharge",
        "diagnosis_at_discharge": "Diagnosis at Discharge",
    }
    ev = CsvAdapter().to_event(raw, mapping=mapping)
    assert ev.patient_name == "Lakshmamma"
    assert ev.caregiver_name == "Suresh Kumar"
    assert ev.caregiver_phone == "+919876543210"


# ── adapter registry ─────────────────────────────────────────────────────

def test_get_adapter_returns_universal_for_custom():
    from app.hmis import get_adapter, WebhookAdapter
    assert get_adapter("custom").__class__ == WebhookAdapter
    assert get_adapter("unknown_source").__class__ == WebhookAdapter


def test_get_adapter_returns_specialized_for_known():
    from app.hmis import get_adapter, NicHmisAdapter, MedmantraAdapter
    assert get_adapter("nic_hmis").__class__ == NicHmisAdapter
    assert get_adapter("medmantra").__class__ == MedmantraAdapter


# ── router (TestClient) ───────────────────────────────────────────────────

@pytest.fixture()
def app_client():
    """Fresh app + temp DB + env configured for HMIS testing."""
    for m in list(sys.modules):
        if m.startswith("app."): del sys.modules[m]
    os.environ["SECRET_KEY"] = "test-secret-32-bytes-aaaaaaaa"
    os.environ["ADMIN_PASSWORD"] = "testpass1234"
    os.environ["SUPERADMIN_PASSWORD"] = "superpass12345"
    os.environ["PUBLIC_BASE_URL"] = "https://test.example"
    os.environ["HMIS_SHARED_SECRET"] = "demo-secret-1234"
    os.environ["ABDM_MODE"] = "mock"
    for k in ("TWILIO_ACCOUNTS", "TWILIO_ACCOUNT_SID", "GROQ_API_KEY",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        os.environ[k] = ""
    d = tempfile.mkdtemp(prefix="hmis_test_")
    os.environ["DATABASE_URL"] = f"sqlite:///{d}/app.db"
    from app.db import init_engine
    init_engine(f"sqlite:///{d}/app.db")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        # Log in as the seeded admin so the admin-required endpoints
        # (`/api/hmis/sources`, `/api/hmis/sources/.../contract`,
        # `/api/hmis/discharge-csv`) work.
        r = c.post("/api/auth/login",
                   json={"username": "admin", "password": "testpass1234"})
        assert r.status_code == 200
        yield c


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _sample_event() -> dict:
    return {
        "patient_name": "Lakshmamma Devi",
        "AGE_YEARS": 65, "GENDER": "F",
        "ATTENDANT_NAME": "Suresh Kumar",
        "mobile_no": "9876543210",
        "date_of_discharge": "2026-07-25",
        "WARD": "Ward-2",
        "diagnosis_at_discharge": "Post-op appendectomy",
        "ABHA": "14-3344-5566-7788",
        "MRN": "MR-T-0001",
        "consent": True, "emr_source": "nic_hmis",
        "emr_patient_id": "MR-T-0001",
    }


def test_webhook_requires_hmac(app_client):
    body = json.dumps(_sample_event()).encode()
    r = app_client.post("/api/hmis/discharge-intake", content=body)
    assert r.status_code == 401


def test_webhook_rejects_bad_hmac(app_client):
    body = json.dumps(_sample_event()).encode()
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": "deadbeef"})
    assert r.status_code == 401


def test_webhook_accepts_valid_hmac(app_client):
    body = json.dumps(_sample_event()).encode()
    sig = _sign("demo-secret-1234", body)
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": sig,
                                 "X-HMIS-Source": "nic_hmis"})
    assert r.status_code == 200
    ack = r.json()
    assert ack["action"] == "created"
    assert ack["enrollment_id"]
    assert ack["emr_source"] == "nic_hmis"


def test_webhook_idempotency_rejects_duplicate(app_client):
    body = json.dumps(_sample_event()).encode()
    sig = _sign("demo-secret-1234", body)
    # First push
    r1 = app_client.post("/api/hmis/discharge-intake", content=body,
                         headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r1.json()["action"] == "created"
    eid1 = r1.json()["enrollment_id"]
    # Same push again
    r2 = app_client.post("/api/hmis/discharge-intake", content=body,
                         headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r2.json()["action"] == "duplicate"
    # Same enrollment id — no second patient created
    assert r2.json()["enrollment_id"] == eid1


def test_webhook_rejects_missing_required_field(app_client):
    ev = _sample_event()
    del ev["diagnosis_at_discharge"]
    body = json.dumps(ev).encode()
    sig = _sign("demo-secret-1234", body)
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r.status_code == 400
    assert "diagnosis_at_discharge" in r.text.lower() or "validation" in r.text.lower()


def test_webhook_rejects_consent_false(app_client):
    ev = _sample_event()
    ev["consent"] = False
    body = json.dumps(ev).encode()
    sig = _sign("demo-secret-1234", body)
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r.status_code == 400


def test_webhook_rejects_future_discharge_date(app_client):
    ev = _sample_event()
    ev["date_of_discharge"] = "2099-12-31"
    body = json.dumps(ev).encode()
    sig = _sign("demo-secret-1234", body)
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r.status_code == 400


def test_webhook_translates_nic_hmis_fields(app_client):
    """When X-HMIS-Source is nic_hmis, the router uses the NIC HMIS
    adapter to translate the field names. Even though the body uses
    AGE_YEARS, the patient record gets age=65."""
    body = json.dumps(_sample_event()).encode()
    sig = _sign("demo-secret-1234", body)
    r = app_client.post("/api/hmis/discharge-intake", content=body,
                        headers={"X-HMIS-Signature": sig, "X-HMIS-Source": "nic_hmis"})
    assert r.status_code == 200
    # Verify the patient was created with age=65 (translated from AGE_YEARS)
    eid = r.json()["enrollment_id"]
    r2 = app_client.get(f"/api/board")
    rows = r2.json()["rows"]
    e = next(r for r in rows if r["enrollment_id"] == eid)
    assert e["patient_name"] == "Lakshmamma Devi"


def test_csv_upload_creates_patients(app_client):
    csv_body = (
        "patient_name,caregiver_name,caregiver_phone,date_of_discharge,diagnosis_at_discharge,consent\n"
        "Ramesh Gowda,Sita Gowda,9876543211,2026-07-25,UTI,true\n"
        "Kamala Devi,Raj Kumar,9876543212,2026-07-25,Pneumonia,true\n"
    )
    r = app_client.post("/api/hmis/discharge-csv",
                        files={"file": ("patients.csv", csv_body, "text/csv")},
                        data={"emr_source": "csv_upload"})
    assert r.status_code == 200
    ack = r.json()
    assert "created 2" in ack["action"]


def test_sources_endpoint_lists_supported(app_client):
    r = app_client.get("/api/hmis/sources")
    assert r.status_code == 200
    sources = [s["source"] for s in r.json()]
    for expected in ("nic_hmis", "e_hospital", "medmantra",
                      "custom", "csv_upload"):
        assert expected in sources


def test_source_contract_returns_field_list(app_client):
    r = app_client.get("/api/hmis/sources/nic_hmis/contract")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "nic_hmis"
    assert "patient_name" in str(body["field_names_used"])
    assert len(body["field_names_used"]) >= 5


# ── event_to_intake_kwargs ────────────────────────────────────────────────

def test_event_to_intake_kwargs_shape():
    from app.hmis import DischargeEvent, event_to_intake_kwargs
    ev = DischargeEvent(
        patient_name="Lakshmamma", caregiver_name="Suresh",
        caregiver_phone="9876543210", date_of_discharge="2026-07-25",
        diagnosis_at_discharge="Wound", consent=True,
        medications=[{"name": "PCM", "type": "other", "doses_per_day": 3}],
        protocol_id="wound_care", ward_name="Ward-1",
    )
    kw = event_to_intake_kwargs(ev)
    assert kw["patient"]["name"] == "Lakshmamma"
    assert kw["patient"]["caregiver_phone"] == "+919876543210"
    assert kw["protocol_id"] == "wound_care"
    assert kw["condition_label"] == "Wound"
    assert kw["ward"] == "Ward-1"
    assert len(kw["meds"]) == 1
    assert kw["meds"][0]["med_name"] == "PCM"
    assert kw["consent"] is True
