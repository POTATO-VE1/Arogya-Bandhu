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

from app.db import SessionLocal
from app.deps import current_user
from app.events import publish
from app.ivr import engine
from app.models import Enrollment, FollowupCall, Patient, User

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