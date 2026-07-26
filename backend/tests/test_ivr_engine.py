"""T7 acceptance: IVR engine driven by a scripted fake transport."""
import json

from app.db import now_utc
from app.ivr import engine
from app.models import (
    CallResponse, Enrollment, EnrollmentMed, Escalation,
    FollowupCall, Patient, User,
)


class FakeTransport:
    def __init__(self):
        self.actions: list[tuple] = []

    def play(self, clip_id):
        self.actions.append(("play", clip_id))

    def expect_digit(self, node_id, options=None, timeout_s=6):
        self.actions.append(("expect", node_id))

    def hangup(self):
        self.actions.append(("hangup",))


def _seed(db, *, with_antibiotic=True, protocol="wound_care") -> str:
    u = User(hospital_code="KA-DIST-01", username="u", password_hash="x",
             display_name="U")
    db.add(u); db.commit(); db.refresh(u)
    p = Patient(hospital_code="KA-DIST-01", name="Lakshmamma", age=58,
                caregiver_name="Ramu", caregiver_phone="+919876543210",
                consent_at=now_utc(), created_by=u.id)
    db.add(p); db.commit(); db.refresh(p)
    e = Enrollment(hospital_code="KA-DIST-01", patient_id=p.id,
                   protocol_id=protocol, condition_label="Post-op",
                   discharge_date="2026-07-25")
    db.add(e); db.commit(); db.refresh(e)
    if with_antibiotic:
        db.add(EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav",
                             med_type="antibiotic", aware_category="Watch",
                             course_days=5, doses_per_day=2))
        db.commit()
    c = FollowupCall(hospital_code="KA-DIST-01", enrollment_id=e.id,
                     day_index=1, scheduled_at=now_utc())
    db.add(c); db.commit(); db.refresh(c)
    return c.id


def _last_expect(t: FakeTransport) -> str | None:
    for a in reversed(t.actions):
        if a[0] == "expect":
            return a[1]
    return None


# ── tests ───────────────────────────────────────────────────────────────────
def test_green_path_with_antibiotic(db):
    cid = _seed(db, with_antibiotic=True)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    assert _last_expect(t) == "confirm_family"
    engine.handle_digit(db, cid, "1", t)   # family yes
    assert _last_expect(t) == "q_wound"
    engine.handle_digit(db, cid, "1", t)   # wound fine
    assert _last_expect(t) == "q_meds_today"
    engine.handle_digit(db, cid, "1", t)   # meds fine
    assert _last_expect(t) == "q_pillcount"
    engine.handle_digit(db, cid, "1", t)  # few pills left
    # edu + closing play then hangup
    assert ("play", "edu_amr") in t.actions
    assert ("play", "closing") in t.actions
    assert t.actions[-1] == ("hangup",)
    call = db.get(FollowupCall, cid)
    assert call.status == "completed"
    assert call.risk_level == "green"
    assert db.query(CallResponse).count() == 4


def test_pillcount_skipped_without_antibiotic(db):
    cid = _seed(db, with_antibiotic=False)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)   # family
    engine.handle_digit(db, cid, "1", t)   # wound
    engine.handle_digit(db, cid, "1", t)  # meds → skips q_pillcount → edu+closing+hangup
    assert ("play", "edu_amr") in t.actions
    assert ("play", "closing") in t.actions
    assert t.actions[-1] == ("hangup",)
    # q_pillcount was skipped: 3 responses only (family, wound, meds)
    assert db.query(CallResponse).count() == 3
    assert db.query(CallResponse).filter(CallResponse.node_id == "q_pillcount").count() == 0
    call = db.get(FollowupCall, cid)
    assert call.status == "completed" and call.risk_level == "green"


def test_red_path_creates_escalation(db):
    cid = _seed(db)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)    # family
    engine.handle_digit(db, cid, "3", t)   # wound: pus/fever → red
    call = db.get(FollowupCall, cid)
    assert call.status == "completed"
    assert call.risk_level == "red"
    esc = db.query(Escalation).first()
    assert esc is not None
    assert esc.status == "open"
    reasons = json.loads(esc.reasons)
    assert "wound: pus/bleeding/fever (SSI red flag)" in reasons
    assert ("play", "red_response") in t.actions


def test_yellow_path(db):
    cid = _seed(db)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)
    engine.handle_digit(db, cid, "2", t)  # wound pain → yellow
    assert ("play", "counsel_yellow") in t.actions
    engine.handle_digit(db, cid, "1", t)  # meds ok
    engine.handle_digit(db, cid, "1", t)  # pillcount ok
    call = db.get(FollowupCall, cid)
    assert call.risk_level == "yellow"
    assert call.status == "completed"


def test_timeout_reprompt_then_exhaust_no_answer(db):
    cid = _seed(db)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)  # family → q_wound
    expect_before = _last_expect(t)
    engine.handle_timeout(db, cid, t)      # first timeout → reprompt + re-expect
    assert ("play", "timeout_reprompt") in t.actions
    assert _last_expect(t) == expect_before
    engine.handle_timeout(db, cid, t)      # second timeout → exhausted
    call = db.get(FollowupCall, cid)
    assert call.status == "no_answer"
    assert t.actions[-1] == ("hangup",)


def test_invalid_digit_counts_as_retry(db):
    cid = _seed(db)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)  # to q_wound
    engine.handle_digit(db, cid, "5", t)  # invalid → reprompt, still on q_wound
    assert ("play", "timeout_reprompt") in t.actions
    assert _last_expect(t) == "q_wound"
    # no response recorded for the invalid digit
    assert db.query(CallResponse).filter(CallResponse.node_id == "q_wound").count() == 0
    # then a valid digit still completes fine
    engine.handle_digit(db, cid, "1", t)


def test_red_hook_invoked_on_red(db):
    seen: list = []
    engine.register_red_hook(lambda d, esc: seen.append(esc.enrollment_id))
    try:
        cid = _seed(db)
        t = FakeTransport()
        engine.start_call(db, cid, t)
        engine.handle_digit(db, cid, "1", t)
        engine.handle_digit(db, cid, "3", t)
        assert len(seen) == 1
    finally:
        engine._red_hooks.clear()