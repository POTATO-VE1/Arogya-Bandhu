"""Tests for the bulk importer (importer.py + API endpoints)."""
import csv
import io
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-32-bytes-random-aaaaa")
os.environ.setdefault("ADMIN_PASSWORD", "changeme123")
os.environ.setdefault("TWILIO_VALIDATE_SIGNATURE", "0")
os.environ.setdefault("PUBLIC_BASE_URL", "https://test.example")
os.environ.setdefault("CALL_ALLOWLIST", "+919876543210")

from app import importer
from app.db import SessionLocal, init_engine
from app.main import app
from app.models import Enrollment, Patient, User
from app.security import hash_password


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode()


VALID_CSV = _make_csv(
    ["Patient Name", "Age", "Sex", "Caregiver Name", "Caregiver Phone",
     "Condition", "Protocol", "Ward", "Medication", "Med Type", "Course Days"],
    [
        ["Lakshmamma", "62", "F", "Ramu", "+919876543210",
         "Post-op appendectomy", "wound_care", "Ward-4", "Amoxiclav 625mg", "antibiotic", "5"],
        ["Manjunath", "45", "M", "Sita", "09876543210",
         "Lower RTI", "antibiotic_course", "Ward-2", "Azithromycin 500mg", "antibiotic", "3"],
    ],
)


# ── importer unit tests ──────────────────────────────────────────────────────

class TestParseFile:
    def test_csv_parse(self):
        rows = importer.parse_file(VALID_CSV, "test.csv")
        assert len(rows) == 2
        assert rows[0]["Patient Name"] == "Lakshmamma"

    def test_empty_csv(self):
        rows = importer.parse_file(b"Patient Name\n", "test.csv")
        assert rows == []

    def test_csv_with_bom(self):
        content = b"\xef\xbb\xbf" + VALID_CSV
        rows = importer.parse_file(content, "test.csv")
        assert len(rows) == 2


class TestSuggestMapping:
    def test_exact_alias_match(self):
        headers = ["Patient Name", "Phone", "Condition", "Protocol"]
        m = importer.suggest_mapping(headers)
        assert m["Patient Name"]["field"] == "name"
        assert m["Phone"]["field"] == "caregiver_phone"
        assert m["Condition"]["field"] == "condition_label"
        assert m["Protocol"]["field"] == "protocol_id"

    def test_fuzzy_match(self):
        headers = ["patient_name", "mobile number", "diagnosis"]
        m = importer.suggest_mapping(headers)
        assert m["patient_name"]["field"] == "name"
        assert m["mobile number"]["field"] == "caregiver_phone"
        assert m["diagnosis"]["field"] == "condition_label"

    def test_unmatched_column(self):
        headers = ["Random Column", "Name"]
        m = importer.suggest_mapping(headers)
        assert m["Random Column"]["field"] is None
        assert m["Name"]["field"] == "name"


class TestNormalise:
    def test_phone_indian_10digit(self):
        assert importer._normalise_phone("9876543210") == "+919876543210"

    def test_phone_with_prefix(self):
        assert importer._normalise_phone("+919876543210") == "+919876543210"

    def test_phone_with_zeros(self):
        assert importer._normalise_phone("00919876543210") == "+919876543210"

    def test_phone_with_dashes(self):
        assert importer._normalise_phone("91-9876-543210") == "+919876543210"

    def test_phone_invalid(self):
        assert importer._normalise_phone("123") is None

    def test_age_plain(self):
        assert importer._normalise_age("62") == 62

    def test_age_with_text(self):
        assert importer._normalise_age("62 years") == 62

    def test_age_invalid(self):
        assert importer._normalise_age("abc") is None

    def test_sex_male(self):
        assert importer._normalise_sex("M") == "M"
        assert importer._normalise_sex("Male") == "M"

    def test_sex_female(self):
        assert importer._normalise_sex("F") == "F"
        assert importer._normalise_sex("Female") == "F"
        assert importer._normalise_sex("ಮಹಿಳೆ") == "F"

    def test_date_iso(self):
        assert importer._normalise_date("2026-07-25") == "2026-07-25"

    def test_date_dmy(self):
        assert importer._normalise_date("25/07/2026") == "2026-07-25"

    def test_date_mdy(self):
        assert importer._normalise_date("07/25/2026") == "2026-07-25"

    def test_protocol_aliases(self):
        assert importer._normalise_protocol("wound care") == "wound_care"
        assert importer._normalise_protocol("Wound_Care") == "wound_care"
        assert importer._normalise_protocol("antibiotic course") == "antibiotic_course"
        assert importer._normalise_protocol("fever viral") == "fever_viral"
        assert importer._normalise_protocol("bad_protocol") is None

    def test_med_type(self):
        assert importer._normalise_med_type("antibiotic") == "antibiotic"
        assert importer._normalise_med_type("ANTIBIOTIC") == "antibiotic"
        assert importer._normalise_med_type("other") == "other"
        assert importer._normalise_med_type("random") == "other"


class TestTemplate:
    def test_generates_csv(self):
        t = importer.generate_template("wound_care")
        assert "Patient Name" in t
        assert "Caregiver Phone" in t
        assert "Lakshmamma" in t  # example row

    def test_all_protocols(self):
        for p in ("wound_care", "antibiotic_course", "fever_viral"):
            t = importer.generate_template(p)
            assert "Patient Name" in t


# ── API endpoint tests ────────────────────────────────────────────────────────

@pytest.fixture()
def _seed(client: TestClient):
    """Seed admin user for auth."""
    s = SessionLocal()
    try:
        if s.query(User).count() == 0:
            s.add(User(
                hospital_code="KA-DIST-01",
                username="admin",
                password_hash=hash_password("changeme123"),
                display_name="Admin",
                role="admin",
            ))
            s.commit()
    finally:
        s.close()


@pytest.fixture()
def _authed(client: TestClient, _seed):
    """Login and return authenticated client."""
    r = client.post("/api/auth/login", json={"username": "admin", "password": "changeme123"})
    assert r.status_code == 200
    return client


class TestImportAPI:
    def test_preview(self, _authed: TestClient):
        r = _authed.post("/api/import/preview", files={"file": ("test.csv", VALID_CSV, "text/csv")})
        assert r.status_code == 200
        d = r.json()
        assert d["total_rows"] == 2
        assert len(d["headers"]) == 11
        assert len(d["rows"]) == 2
        assert "file_id" in d

    def test_preview_empty(self, _authed: TestClient):
        r = _authed.post("/api/import/preview", files={"file": ("empty.csv", b"Name\n", "text/csv")})
        assert r.status_code == 400

    def test_confirm_import(self, _authed: TestClient):
        # preview first
        r = _authed.post("/api/import/preview", files={"file": ("test.csv", VALID_CSV, "text/csv")})
        d = r.json()
        file_id = d["file_id"]

        # build mapping
        mapping = {}
        for h in d["headers"]:
            info = d["mapping_suggestions"].get(h, {})
            mapping[h] = info

        # confirm
        r2 = _authed.post("/api/import/confirm", json={
            "file_id": file_id,
            "mapping": mapping,
            "selected_indices": [0, 1],
            "default_protocol": "wound_care",
        })
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["imported"] == 2
        assert d2["skipped"] == 0
        assert len(d2["enrollment_ids"]) == 2

    def test_template(self, _authed: TestClient):
        r = _authed.get("/api/import/template/wound_care")
        assert r.status_code == 200
        assert "Patient Name" in r.text
