"""T10–T13b acceptance."""
import json
from datetime import datetime, timedelta, timezone

from app.db import now_utc
from app.models import (CallResponse, Enrollment, EnrollmentMed, Escalation,
                        FollowupCall, Patient, User)

ADMIN = {"username": "admin", "password": "changeme123"}


def _login(client):
    client.post("/api/auth/login", json=ADMIN)


def _full_enroll(db, name="Lakshmamma", protocol="wound_care", verified=False) -> str:
    u = db.query(User).first() or User(hospital_code="KA-DIST-01", username="u",
                                       password_hash="x", display_name="U",
                                       role="staff", created_at=now_utc())
    db.add(u) if u not in db else None
    p = Patient(hospital_code="KA-DIST-01", name=name, age=58,
                caregiver_name="Ramu", caregiver_phone="+919876543210",
                consent_at=now_utc(), created_by=u.id)
    db.add(p); db.commit(); db.refresh(p)
    e = Enrollment(hospital_code="KA-DIST-01", patient_id=p.id, protocol_id=protocol,
                   condition_label="Post-op", discharge_date="2026-07-25",
                   number_verified=1 if verified else 0)
    db.add(e); db.commit(); db.refresh(e)
    db.add(EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav", med_type="antibiotic",
                         doses_per_day=2))
    db.commit()
    return e.id


# ── T11 escalations ────────────────────────────────────────────────────────────
def test_escalation_ack_sets_fields_and_audit(client, db):
    _login(client)
    eid = _full_enroll(db, verified=True)
    c = FollowupCall(hospital_code="KA-DIST-01", enrollment_id=eid, day_index=1,
                     scheduled_at=now_utc(), status="completed",
                     risk_level="red", risk_score=10, risk_reasons='["SSI"]')
    db.add(c); db.commit(); db.refresh(c)
    esc = Escalation(hospital_code="KA-DIST-01", enrollment_id=eid, call_id=c.id,
                     reasons='["SSI red flag"]')
    db.add(esc); db.commit(); db.refresh(esc)

    r = client.get("/api/escalations")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["status"] == "open"
    assert "•••" in rows[0]["caregiver_phone"]  # masked

    a = client.post(f"/api/escalations/{esc.id}/ack")
    assert a.status_code == 200 and a.json()["status"] == "acked"
    from app.models import AuditLog
    assert db.query(AuditLog).filter(AuditLog.action == "ack").count() == 1


def test_escalation_cross_hospital_404(client, db):
    _login(client)
    eid = _full_enroll(db)
    esc = Escalation(hospital_code="OTHER", enrollment_id=eid, reasons="[]")
    db.add(esc); db.commit(); db.refresh(esc)
    r = client.post(f"/api/escalations/{esc.id}/ack")
    assert r.status_code == 404


# ── T13 FHIR ──────────────────────────────────────────────────────────────────
def test_fhir_export_structure(client, db):
    _login(client)
    eid = _full_enroll(db)
    e = db.get(Enrollment, eid)
    r = client.get(f"/api/patients/{e.patient_id}/fhir")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    types = [x["resource"]["resourceType"] for x in bundle["entry"]]
    assert "Composition" in types and "Patient" in types
    comp = next(x["resource"] for x in bundle["entry"]
                if x["resource"]["resourceType"] == "Composition")
    assert comp["type"]["coding"][0]["code"] == "373942005"
    meds = [x["resource"] for x in bundle["entry"]
            if x["resource"]["resourceType"] == "MedicationRequest"]
    assert len(meds) == 1


def test_fhir_cross_hospital_404(client, db):
    _login(client)
    uid = db.query(User).first().id
    p = Patient(hospital_code="OTHER", name="X", caregiver_name="Y",
                caregiver_phone="+919900000000", consent_at=now_utc(), created_by=uid)
    db.add(p); db.commit()
    r = client.get(f"/api/patients/{p.id}/fhir")
    assert r.status_code == 404


# ── T13b LLM assist ────────────────────────────────────────────────────────────
def test_suggest_503_when_disabled(client, db):
    _login(client)
    r = client.post("/api/enrollments/suggest",
                    json={"condition_label": "appendectomy post-op"})
    assert r.status_code == 503  # no GROQ_API_KEY in tests


def test_llm_failure_matrix(monkeypatch):
    from app import llm
    # disabled path explicitly
    monkeypatch.setattr(llm, "enabled", lambda: False)
    assert llm.suggest_protocol("x") is None
    assert llm.personalize_sheet(["a"], "x") is None
    monkeypatch.setattr(llm, "enabled", lambda: True)
    # http failure
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr("app.llm.httpx.post", boom)
    assert llm.suggest_protocol("x") is None
    assert llm.personalize_sheet(["a", "b"], "x") is None
    # fake good response
    class R:
        def __init__(self, content): self._c = content
        @property
        def status_code(self): return 200
        def json(self): return {"choices": [{"message": {"content": self._c}}]}
    good = R(json.dumps({"protocol_id": "wound_care",
                         "instructions_en": ["keep wound dry", "rest well"],
                         "note": "ok"}))
    monkeypatch.setattr("app.llm.httpx.post", lambda *a, **k: good)
    out = llm.suggest_protocol("appendectomy")
    assert out is not None
    assert out["protocol_id"] == "wound_care" and out["source"] == "llm"
    # dosage guard trips → None
    guardy = R(json.dumps({"protocol_id": "wound_care",
                           "instructions_en": ["take 500mg amoxicillin daily"],
                           "note": "x"}))
    monkeypatch.setattr("app.llm.httpx.post", lambda *a, **k: guardy)
    assert llm.suggest_protocol("x") is None
    # sheet good index selection
    sheet_good = R(json.dumps({"bullet_indices": [1, 3]}))
    monkeypatch.setattr("app.llm.httpx.post", lambda *a, **k: sheet_good)
    s = llm.personalize_sheet(["b0", "b1", "b2", "b3"], "x")
    assert s is not None
    assert s == {"bullets_kn": ["b1", "b3"], "source": "llm"}
    # sheet out-of-range index → None
    sheet_bad = R(json.dumps({"bullet_indices": [9]}))
    monkeypatch.setattr("app.llm.httpx.post", lambda *a, **k: sheet_bad)
    assert llm.personalize_sheet(["b0"], "x") is None


def test_enroll_sets_template_sheet_when_llm_disabled(client, db):
    _login(client)
    body = {
        "patient": {"name": "X", "age": 50, "sex": "F", "abha_number": None,
                    "caregiver_name": "R", "caregiver_phone": "+919876543210"},
        "protocol_id": "wound_care", "condition_label": "Post-op",
        "discharge_date": "2026-07-25", "meds": [], "consent": True,
    }
    r = client.post("/api/enrollments", json=body)
    assert r.status_code == 201
    eid = r.json()["enrollment_id"]
    e = db.get(Enrollment, eid)
    sheet = json.loads(e.sheet_instructions)
    assert sheet["source"] == "template"
    assert len(sheet["bullets_kn"]) == 5