"""Demo Call Console — the in-browser IVR simulator (docs/04 §4).

Drives the SAME engine (docs/04 §1) over WebSocket. NOT a phone: no dial pad, no
number entry. Useful for development (no Twilio minutes) and as the demo-day
fallback when venue Wi-Fi dies — same call responses, same escalations as Twilio.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import SessionLocal, get_db, now_utc
from app.deps import current_user
from app.events import publish
from app.ivr import engine
from app.models import Enrollment, Escalation, FollowupCall, Patient, User

log = logging.getLogger("sim")
router = APIRouter(tags=["sim"])


class _SimTransport:
    def __init__(self, loop: asyncio.AbstractEventLoop, out_q: asyncio.Queue):
        self._loop = loop
        self._out = out_q

    def _emit(self, msg: dict) -> None:
        self._loop.call_soon_threadsafe(self._out.put_nowait, msg)

    def play(self, clip_id: str) -> None:
        from app.protocol_loader import get_deck
        en = get_deck().get(clip_id, {}).get("en", "")
        self._emit({"type": "play", "clip": clip_id, "en": en})

    def expect_digit(self, node_id: str, options: dict | None = None,
                     timeout_s: int = 6) -> None:
        opts = []
        if options:
            for digit, opt in options.items():
                opts.append({"digit": digit, "reason": opt.get("reason"),
                             "clip": opt.get("clip"), "next": opt.get("next")})
        self._emit({"type": "expect_digit", "node_id": node_id, "options": opts})

    def hangup(self) -> None:
        self._emit({"type": "end"})


async def _run(call_id: str, transport: _SimTransport, fn) -> None:
    def _():
        s = SessionLocal()
        try:
            fn(s, call_id, transport)
        finally:
            s.close()
    await run_in_threadpool(_)


@router.websocket("/ws/sim-call")
async def sim_call(ws: WebSocket):
    # session authenticated via cookie (sent automatically by the browser)
    session_user = ws.session.get("user_id")
    if not session_user:
        await ws.close(code=4403)
        return

    call_id = ws.query_params.get("call_id", "")
    s = SessionLocal()
    try:
        call = s.query(FollowupCall).filter(FollowupCall.id == call_id).first()
        if not call or call.kind != "demo":
            await ws.close(code=4404)
            return
        en = s.query(Enrollment).filter(Enrollment.id == call.enrollment_id).first()
        if not en:
            await ws.close(code=4404)
            return
    finally:
        s.close()

    await ws.accept()
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue = asyncio.Queue()
    transport = _SimTransport(loop, out_q)

    async def sender():
        try:
            while True:
                msg = await out_q.get()
                if msg is None:
                    break
                await ws.send_json(msg)
                if msg.get("type") == "end":
                    # final flush then close
                    publish("call_update", call.enrollment_id)
                    return
        except Exception:
            pass

    sender_task = asyncio.create_task(sender())
    try:
        await _run(call_id, transport, lambda db, cid, t: engine.start_call(db, cid, t))
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "digit":
                digit = str(msg.get("digit"))
                await _run(call_id, transport,
                           lambda db, cid, t: engine.handle_digit(db, cid, digit, t))
    except WebSocketDisconnect:
        pass
    finally:
        await out_q.put(None)
        try:
            await sender_task
        except Exception:
            pass

# ── scripted demo: drive a sim call to red with one click ──────────────────
# The judge watches a full call go: greet → confirm family → questions →
# patient says "wound has pus + bleeding" → red escalation created.
# Used by the one-click "demo escalation scenario" button on the Board.
# This is a synchronous endpoint that drives the same engine the WebSocket
# uses, but pre-supplies the answers that lead to @end_red.

# Node → digit mapping chosen so the call takes the "red flag" branch at
# q_wound (digit 3 = "wound: pus/bleeding/fever (SSI red flag) → @end_red").
SCRIPTED_RED_ANSWERS = {
    "confirm_family": "1",   # yes, this is the right person
    "q_wound": "3",          # pus / bleeding / fever (RED)
}


class ScriptedSim:
    """In-process simulation transport. Same interface as _SimTransport
    but writes events to a list instead of a WebSocket — used by the
    scripted /api/demo/scripted-red endpoint so the judge can click
    once and see a full red call on the dashboard."""

    def __init__(self):
        self.events: list[dict] = []
        self._expect_node: str | None = None

    def play(self, clip_id: str) -> None:
        from app.protocol_loader import get_deck
        en = get_deck().get(clip_id, {}).get("en", "")
        self.events.append({"type": "play", "clip": clip_id, "en": en})

    def expect_digit(self, node_id: str, options: dict | None = None,
                     timeout_s: int = 6) -> None:
        opts = []
        if options:
            for digit, opt in options.items():
                opts.append({"digit": digit, "reason": opt.get("reason")})
        self.events.append({"type": "expect_digit", "node_id": node_id, "options": opts})
        self._expect_node = node_id

    def hangup(self) -> None:
        self.events.append({"type": "end"})


@router.post("/api/demo/scripted-red")
def scripted_red(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One-click demo: drive a fresh sim call all the way to @end_red
    using scripted patient answers. Creates a new FollowupCall, walks
    the engine, returns the event log + the resulting Escalation (if
    any) so the dashboard reflects it in real time.

    Returns:
      { call_id, risk_level, escalation_id, events: [...] }
    """
    e = db.query(Enrollment).filter(
        Enrollment.status == "active",
    ).order_by(Enrollment.created_at.desc()).first()
    if not e:
        raise HTTPException(503, "no active enrollment to demo with")

    c = FollowupCall(
        hospital_code=e.hospital_code, enrollment_id=e.id,
        day_index=0, scheduled_at=now_utc(), provider="sim",
        kind="demo", status="pending", triggered_by=user.id,
    )
    db.add(c); db.commit(); db.refresh(c)
    call_id = c.id

    transport = ScriptedSim()
    # Drive start_call
    engine.start_call(db, call_id, transport)
    # Walk through questions with scripted answers until terminal
    max_steps = 50
    while max_steps > 0:
        max_steps -= 1
        if not transport._expect_node:
            break
        node = transport._expect_node
        digit = SCRIPTED_RED_ANSWERS.get(node, "1")
        engine.handle_digit(db, call_id, digit, transport)

    db.refresh(c)
    result = {
        "call_id": call_id,
        "patient_id": e.patient_id,
        "enrollment_id": e.id,
        "risk_level": c.risk_level,
        "risk_reasons": c.risk_reasons,
        "status": c.status,
        "events": transport.events,
    }
    if c.risk_level == "red":
        esc = db.query(Escalation).filter(
            Escalation.call_id == call_id,
        ).first()
        if esc:
            result["escalation_id"] = esc.id
            result["escalation_level"] = esc.level
    return result
