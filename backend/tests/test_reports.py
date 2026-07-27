"""Tests for patient report upload, listing, and download."""
import io

from app.health_fit import PatientReport
from app.models import Enrollment, Patient, User
from app.db import now_utc

ADMIN = {"username": "admin", "password": "changeme123"}


def _login(client):
    client.post("/api/auth/login", json=ADMIN)


def _seed_enrollment(client, db) -> str:
    """Seed a patient + enrollment via the API and return enrollment_id."""
    _login(client)
    body = {
        "patient": {"name": "TestPatient", "age": 40, "sex": "M",
                    "caregiver_name": "CareGiver", "caregiver_phone": "+919876543210"},
        "protocol_id": "wound_care",
        "condition_label": "Post-op test",
        "ward": "Ward-1",
        "discharge_date": "2026-07-25",
        "meds": [],
        "consent": True,
    }
    r = client.post("/api/enrollments", json=body)
    assert r.status_code == 201, r.text
    return r.json()["enrollment_id"]


def _make_file(name: str, content: bytes = b"fake-pdf-data", size: int | None = None):
    """Create a fake UploadFile via (name, content, media_type) tuple for TestClient."""
    data = content if size is None else b"\x00" * size
    return (name, io.BytesIO(data), "application/octet-stream")


class TestUploadReports:
    def test_upload_single_pdf(self, client, db):
        eid = _seed_enrollment(client, db)
        files = {"files": ("discharge.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["uploaded"]) == 1
        report = body["uploaded"][0]
        assert report["report_type"] == "pdf"
        assert "id" in report
        assert report["uploaded_at"] is not None

    def test_upload_multiple_files(self, client, db):
        eid = _seed_enrollment(client, db)
        files = [
            ("files", ("report1.pdf", io.BytesIO(b"%PDF data1"), "application/pdf")),
            ("files", ("scan.jpg", io.BytesIO(b"\xff\xd8\xff\xe0jpg"), "image/jpeg")),
            ("files", ("doc.docx", io.BytesIO(b"PK\x03\x04docx"), "application/msword")),
        ]
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["uploaded"]) == 3
        types = {u["report_type"] for u in body["uploaded"]}
        assert types == {"pdf", "jpg", "docx"}

    def test_rejects_bad_extension(self, client, db):
        eid = _seed_enrollment(client, db)
        files = {"files": ("malware.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 400
        assert "not allowed" in r.json()["detail"]

    def test_rejects_oversized_file(self, client, db):
        eid = _seed_enrollment(client, db)
        big = b"\x00" * (10 * 1024 * 1024 + 1)  # 10MB + 1 byte
        files = {"files": ("huge.pdf", io.BytesIO(big), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 400
        assert "10 MB" in r.json()["detail"]

    def test_404_for_wrong_hospital(self, client, db):
        """An enrollment from a different hospital should 404."""
        eid = _seed_enrollment(client, db)
        # Change user's hospital_code to simulate different hospital
        u = db.query(User).filter(User.username == "admin").first()
        orig = u.hospital_code
        u.hospital_code = "OTHER-HOSPITAL"
        db.commit()
        files = {"files": ("test.pdf", io.BytesIO(b"%PDF"), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        assert r.status_code == 404
        # Restore
        u.hospital_code = orig
        db.commit()

    def test_401_without_session(self, client, db):
        r = client.post("/api/enrollments/fake/reports",
                        files={"files": ("x.pdf", io.BytesIO(b"x"), "application/pdf")})
        assert r.status_code == 401


class TestListReports:
    def test_list_empty(self, client, db):
        eid = _seed_enrollment(client, db)
        r = client.get(f"/api/enrollments/{eid}/reports")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_upload(self, client, db):
        eid = _seed_enrollment(client, db)
        files = [
            ("files", ("a.pdf", io.BytesIO(b"%PDF-1"), "application/pdf")),
            ("files", ("b.png", io.BytesIO(b"\x89PNG"), "image/png")),
        ]
        client.post(f"/api/enrollments/{eid}/reports", files=files)
        r = client.get(f"/api/enrollments/{eid}/reports")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        assert all("id" in i and "filename" in i and "report_type" in i for i in items)

    def test_list_404_for_wrong_hospital(self, client, db):
        eid = _seed_enrollment(client, db)
        u = db.query(User).filter(User.username == "admin").first()
        orig = u.hospital_code
        u.hospital_code = "OTHER-HOSPITAL"
        db.commit()
        r = client.get(f"/api/enrollments/{eid}/reports")
        assert r.status_code == 404
        u.hospital_code = orig
        db.commit()


class TestDownloadReport:
    def test_download_roundtrip(self, client, db):
        eid = _seed_enrollment(client, db)
        content = b"%PDF-1.4 test content"
        files = {"files": ("test.pdf", io.BytesIO(content), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        report_id = r.json()["uploaded"][0]["id"]

        r = client.get(f"/api/reports/{report_id}/download")
        assert r.status_code == 200
        assert r.content == content
        assert "test.pdf" in r.headers.get("content-disposition", "")

    def test_download_404_for_wrong_hospital(self, client, db):
        eid = _seed_enrollment(client, db)
        files = {"files": ("test.pdf", io.BytesIO(b"%PDF"), "application/pdf")}
        r = client.post(f"/api/enrollments/{eid}/reports", files=files)
        report_id = r.json()["uploaded"][0]["id"]

        u = db.query(User).filter(User.username == "admin").first()
        orig = u.hospital_code
        u.hospital_code = "OTHER-HOSPITAL"
        db.commit()
        r = client.get(f"/api/reports/{report_id}/download")
        assert r.status_code == 404
        u.hospital_code = orig
        db.commit()

    def test_download_404_for_bogus_id(self, client, db):
        _login(client)
        r = client.get("/api/reports/nonexistent123/download")
        assert r.status_code == 404
