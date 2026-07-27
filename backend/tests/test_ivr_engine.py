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


def _seed(db, *, protocol="wound_care", username="u") -> str:
    u = User(hospital_code="KA-DIST-01", username=username, password_hash="x",
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
    db.add(EnrollmentMed(enrollment_id=e.id, med_name="Amoxiclav",
                         med_type="antibiotic", doses_per_day=2))
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
def test_green_path(db):
    cid = _seed(db)
    t = FakeTransport()
    engine.start_call(db, cid, t)
    assert _last_expect(t) == "confirm_family"
    engine.handle_digit(db, cid, "1", t)   # family yes
    assert _last_expect(t) == "q_wound"
    engine.handle_digit(db, cid, "1", t)   # wound fine
    assert _last_expect(t) == "q_meds_today"
    engine.handle_digit(db, cid, "1", t)   # meds fine
    # closing play then hangup
    assert ("play", "closing") in t.actions
    assert t.actions[-1] == ("hangup",)
    call = db.get(FollowupCall, cid)
    assert call.status == "completed"
    assert call.risk_level == "green"
    assert db.query(CallResponse).count() == 3


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


def test_fever_viral_path(db):
    cid = _seed(db, protocol="fever_viral")
    t = FakeTransport()
    engine.start_call(db, cid, t)
    assert _last_expect(t) == "confirm_family"
    engine.handle_digit(db, cid, "1", t)   # family yes
    assert _last_expect(t) == "q_fever"
    engine.handle_digit(db, cid, "1", t)   # no fever
    assert _last_expect(t) == "q_breath"
    engine.handle_digit(db, cid, "1", t)   # no breathlessness
    assert ("play", "closing") in t.actions
    assert t.actions[-1] == ("hangup",)
    call = db.get(FollowupCall, cid)
    assert call.status == "completed"
    assert call.risk_level == "green"


def test_antibiotic_course_path(db):
    cid = _seed(db, protocol="antibiotic_course")
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)   # family yes
    assert _last_expect(t) == "q_symptom_course"
    engine.handle_digit(db, cid, "1", t)   # better
    assert _last_expect(t) == "q_meds_today"
    engine.handle_digit(db, cid, "1", t)   # meds ok
    # T3: new pill-count question (only on antibiotic_course)
    assert _last_expect(t) == "q_pillcount_remaining"
    engine.handle_digit(db, cid, "1", t)   # 0-3 remaining → adherent
    assert ("play", "closing") in t.actions
    assert t.actions[-1] == ("hangup",)
    call = db.get(FollowupCall, cid)
    assert call.status == "completed"
    assert call.risk_level == "green"


def test_antibiotic_pillcount_forces_red_on_last_day(db):
    """T3: on the last scheduled day of an antibiotic protocol, '8+ remaining'
    forces red (J5 — the patient didn't finish the course)."""
    from app.models import FollowupCall
    cid = _seed(db, protocol="antibiotic_course")
    # Move the call to the last scheduled day (Day 7 for antibiotic_course)
    call = db.get(FollowupCall, cid)
    call.day_index = 7   # max(schedule_days) for antibiotic_course
    db.commit()
    t = FakeTransport()
    engine.start_call(db, cid, t)
    engine.handle_digit(db, cid, "1", t)   # family yes
    engine.handle_digit(db, cid, "1", t)   # symptoms better
    engine.handle_digit(db, cid, "1", t)   # meds ok
    # pillcount = 3 (8+ remaining), on the last day → forced red
    engine.handle_digit(db, cid, "3", t)
    assert ("play", "closing") in t.actions
    db.refresh(call)
    assert call.status == "completed"
    assert call.risk_level == "red"
    assert any("8+" in r for r in json.loads(call.risk_reasons))


def test_antibiotic_pillcount_score_alone_yellow_then_pillcount_pushes_red(db):
    """On an early day, score 2 (4-7 remaining) + 0 = yellow, but the
    forced-red override on the LAST day bumps to red."""
    from app.models import FollowupCall
    # First call: day 1, partial adherence (4-7) — should be yellow
    cid = _seed(db, protocol="antibiotic_course", username="u_pillcount_early")
    t1 = FakeTransport()
    engine.start_call(db, cid, t1)
    engine.handle_digit(db, cid, "1", t1)
    engine.handle_digit(db, cid, "1", t1)  # better
    engine.handle_digit(db, cid, "1", t1)  # meds ok
    engine.handle_digit(db, cid, "2", t1)  # 4-7 remaining → score 2
    c1 = db.get(FollowupCall, cid)
    assert c1.risk_level == "yellow"
    assert c1.risk_score == 2

    # Second call: day 7 (last), 4-7 remaining — would be yellow without the
    # override (score 2), but the T3 rule forces red.
    cid2 = _seed(db, protocol="antibiotic_course", username="u_pillcount_last")
    call2 = db.get(FollowupCall, cid2)
    call2.day_index = 7
    db.commit()
    t2 = FakeTransport()
    engine.start_call(db, cid2, t2)
    engine.handle_digit(db, cid2, "1", t2)
    engine.handle_digit(db, cid2, "1", t2)
    engine.handle_digit(db, cid2, "1", t2)
    engine.handle_digit(db, cid2, "2", t2)  # 4-7 remaining on last day
    db.refresh(call2)
    assert call2.risk_level == "red"   # forced by T3
    assert any("4-7" in r for r in json.loads(call2.risk_reasons))
