"""Provider-agnostic IVR call state machine (docs/04 §1–2).

Pure logic over a `Transport` seam. Twilio (T8) and the Demo Call Console (T16)
are two thin transports that drive the *same* machine. All state lives in the DB
(`followup_calls.current_node`, `node_retries`, `call_responses`) so an app
restart mid-call-flow loses nothing (docs/02 §1.4).

Skip rules (docs/04 §2):
  requires_antibiotic   – skip a question node when the enrollment has no antibiotic
  min_day_vs_course_end – skip when day_index < the longest antibiotic course_days
In both cases the machine advances via option "1"'s `next` without recording a
response or scoring (the "all-fine" path; we never fabricate a clinical answer).
"""
from __future__ import annotations

import json
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app.db import now_utc
from app.events import publish as _publish_event
from app.models import CallResponse, Escalation, EnrollmentMed, FollowupCall
from app.protocol_loader import get_deck, get_protocol
from app.risk import RiskResult, evaluate

TERMINALS = {"@end_ok", "@end_red", "@end_noanswer"}


class Transport(Protocol):
    def play(self, clip_id: str) -> None: ...
    def expect_digit(self, node_id: str, options: dict | None = None,
                     timeout_s: int = 6) -> None: ...
    def hangup(self) -> None: ...


# ── extension hooks (T10 telegram + later SSE register here) ─────────────────
_red_hooks: list[Callable[[Session, Escalation], None]] = []


def register_red_hook(fn: Callable[[Session, Escalation], None]) -> None:
    _red_hooks.append(fn)


# ── context helpers ───────────────────────────────────────────────────────────
def _ctx(db: Session, call_id: str):
    call = db.query(FollowupCall).filter(FollowupCall.id == call_id).first()
    if not call:
        raise RuntimeError(f"unknown call {call_id}")
    proto = get_protocol(call.enrollment.protocol_id)
    meds = db.query(EnrollmentMed).filter(
        EnrollmentMed.enrollment_id == call.enrollment_id).all()
    has_abx = any(m.med_type == "antibiotic" for m in meds)
    course_end = max((m.course_days or 0 for m in meds
                      if m.med_type == "antibiotic"), default=0)
    return call, proto, has_abx, course_end


def _skip_node(node: dict, has_abx: bool, day_index: int, course_end: int) -> bool:
    if node.get("requires_antibiotic") and not has_abx:
        return True
    if node.get("min_day_vs_course_end") and day_index < course_end:
        return True
    return False


def _step(db: Session, call: FollowupCall, proto: dict,
          has_abx: bool, course_end: int, transport: Transport) -> None:
    """Drive from call.current_node until a question (expect) or terminal."""
    nodes = proto["nodes"]
    while True:
        node_id = call.current_node
        if node_id in TERMINALS:
            finish_call(db, call.id, node_id)
            transport.hangup()
            return
        if node_id not in nodes:
            raise RuntimeError(f"broken protocol: node '{node_id}' not found in '{call.enrollment.protocol_id}'")
        node = nodes[node_id]
        if node["type"] == "play":
            transport.play(node["clip"])
            call.current_node = node["next"]
            continue
        # question node
        if _skip_node(node, has_abx, call.day_index, course_end):
            call.current_node = node["options"]["1"]["next"]
            continue
        transport.play(node["clip"])
        call.node_retries = 0
        transport.expect_digit(node_id, options=node.get("options"), timeout_s=6)
        db.commit()
        return


def start_call(db: Session, call_id: str, transport: Transport) -> None:
    call, proto, has_abx, course_end = _ctx(db, call_id)
    call.status = "in_progress"
    call.started_at = now_utc()
    call.current_node = proto["start_node"]
    db.commit()
    _step(db, call, proto, has_abx, course_end, transport)


def handle_digit(db: Session, call_id: str, digit: str, transport: Transport) -> None:
    call, proto, has_abx, course_end = _ctx(db, call_id)
    if call.status != "in_progress":
        return  # stale webhook
    node = proto["nodes"][call.current_node]
    opts = node["options"]
    if digit not in opts:
        _retry(db, call, proto, has_abx, course_end, transport, node)
        return
    opt = opts[digit]
    db.add(CallResponse(call_id=call_id, node_id=call.current_node,
                        digit=digit, score=int(opt["score"])))
    db.commit()
    if "clip" in opt:
        transport.play(opt["clip"])
    call.current_node = opt["next"]
    db.commit()
    _step(db, call, proto, has_abx, course_end, transport)


def handle_timeout(db: Session, call_id: str, transport: Transport) -> None:
    call, proto, has_abx, course_end = _ctx(db, call_id)
    if call.status != "in_progress":
        return
    node = proto["nodes"][call.current_node]
    _retry(db, call, proto, has_abx, course_end, transport, node)


def _retry(db, call, proto, has_abx, course_end, transport, node) -> None:
    allowed = int(node.get("retries", 1))
    call.node_retries += 1
    db.commit()
    if call.node_retries <= allowed:
        transport.play("timeout_reprompt")
        transport.expect_digit(call.current_node, timeout_s=6)
        db.commit()
        return
    # exhausted
    call.current_node = "@end_noanswer"
    db.commit()
    _step(db, call, proto, has_abx, course_end, transport)


# ── risk + escalation (docs/03 §6.3) ─────────────────────────────────────────
def _derive_outcomes(db: Session, call: FollowupCall, proto: dict) -> list[dict]:
    resps = db.query(CallResponse).filter(CallResponse.call_id == call.id).all()
    nodes = proto["nodes"]
    out = []
    for r in resps:
        opt = nodes[r.node_id]["options"][r.digit]
        out.append({
            "node_id": r.node_id, "digit": r.digit,
            "score": r.score, "reason": opt.get("reason"),
            "forced_red": opt["next"] == "@end_red",
        })
    return out


def finish_call(db: Session, call_id: str, terminal: str) -> RiskResult:
    call, proto, _has_abx, _course_end = _ctx(db, call_id)
    missed_before = db.query(FollowupCall).filter(
        FollowupCall.enrollment_id == call.enrollment_id,
        FollowupCall.status == "no_answer",
        FollowupCall.id != call.id,
    ).count()
    result = evaluate(_derive_outcomes(db, call, proto), missed_calls_before=missed_before)

    call.risk_level = result.level
    call.risk_score = result.score
    call.risk_reasons = json.dumps(result.reasons, ensure_ascii=False)
    call.status = "completed" if terminal in ("@end_ok", "@end_red") else "no_answer"
    call.completed_at = now_utc()
    db.commit()
    _publish_event("call_update", call.enrollment_id)

    if result.level == "red":
        existing = db.query(Escalation).filter(
            Escalation.enrollment_id == call.enrollment_id,
            Escalation.status == "open",
        ).first()
        if not existing:
            esc = Escalation(
                hospital_code=call.hospital_code,
                enrollment_id=call.enrollment_id, call_id=call.id,
                reasons=call.risk_reasons,
            )
            db.add(esc)
            db.commit()
            db.refresh(esc)
            _publish_event("escalation", esc.id)
            for hook in _red_hooks:
                try:
                    hook(db, esc)
                except Exception:
                    pass  # never let a notify failure break a call (docs/04 §6)
    return result