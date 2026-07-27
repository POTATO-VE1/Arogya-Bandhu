"""Provider-agnostic IVR call state machine (docs/04 §1–2).

Pure logic over a `Transport` seam. Twilio (T8) and the Demo Call Console (T16)
are two thin transports that drive the *same* machine. All state lives in the DB
(`followup_calls.current_node`, `node_retries`, `call_responses`) so an app
restart mid-call-flow loses nothing (docs/02 §1.4).
"""
from __future__ import annotations

import json
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app.db import now_utc
from app.events import publish as _publish_event
from app.models import CallResponse, Escalation, FollowupCall
from app.protocol_loader import get_protocol
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
    return call, proto


def _step(db: Session, call: FollowupCall, proto: dict,
          transport: Transport) -> None:
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
        transport.play(node["clip"])
        call.node_retries = 0
        transport.expect_digit(node_id, options=node.get("options"), timeout_s=6)
        db.commit()
        return


def start_call(db: Session, call_id: str, transport: Transport) -> None:
    call, proto = _ctx(db, call_id)
    call.status = "in_progress"
    call.started_at = now_utc()
    call.current_node = proto["start_node"]
    db.commit()
    _step(db, call, proto, transport)


def handle_digit(db: Session, call_id: str, digit: str, transport: Transport) -> None:
    call, proto = _ctx(db, call_id)
    if call.status != "in_progress":
        return  # stale webhook
    node = proto["nodes"][call.current_node]
    opts = node["options"]
    if digit not in opts:
        _retry(db, call, proto, transport, node)
        return
    opt = opts[digit]
    db.add(CallResponse(call_id=call_id, node_id=call.current_node,
                        digit=digit, score=int(opt["score"])))
    db.commit()
    if "clip" in opt:
        transport.play(opt["clip"])
    call.current_node = opt["next"]
    db.commit()
    _step(db, call, proto, transport)


def handle_timeout(db: Session, call_id: str, transport: Transport) -> None:
    call, proto = _ctx(db, call_id)
    if call.status != "in_progress":
        return
    node = proto["nodes"][call.current_node]
    _retry(db, call, proto, transport, node)


def _retry(db, call, proto, transport, node) -> None:
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
    _step(db, call, proto, transport)


# ── risk + escalation (docs/03 §6.3) ─────────────────────────────────────────
def _derive_outcomes(db: Session, call: FollowupCall, proto: dict) -> list[dict]:
    """Build the outcomes list for the risk engine.

    T3 (docs/09_PLAN.md) adds the pill-count "course should be done"
    rule: if the call is on the protocol's last scheduled day (i.e. the
    course should be finished by now) AND the family reports 8+ pills
    remaining, force red — "you still have half the strip, you didn't
    finish" is a hard adherence signal, not a soft one.
    """
    resps = db.query(CallResponse).filter(CallResponse.call_id == call.id).all()
    nodes = proto["nodes"]
    schedule = proto.get("schedule_days", []) or []
    last_day = max(schedule) if schedule else None
    out = []
    for r in resps:
        opt = nodes[r.node_id]["options"][r.digit]
        forced_red = opt["next"] == "@end_red"
        # T3: pill-count forced-red override. On the protocol's last
        # scheduled day, any non-adherent pill-count answer (4-7 or 8+)
        # forces red — "you should have ~0 pills left by now" is a hard
        # adherence signal regardless of the score threshold.
        if (not forced_red
            and r.node_id == "q_pillcount_remaining"
            and r.digit in ("2", "3")
            and last_day is not None
            and call.day_index >= last_day):
            forced_red = True
        out.append({
            "node_id": r.node_id, "digit": r.digit,
            "score": r.score, "reason": opt.get("reason"),
            "forced_red": forced_red,
        })
    return out


def finish_call(db: Session, call_id: str, terminal: str) -> RiskResult:
    call, proto = _ctx(db, call_id)
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
