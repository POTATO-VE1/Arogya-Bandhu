"""Twilio webhooks (docs/04 §3). Stateless: every hit reads DB, drives the engine,
renders TwiML. Signature-validated when TWILIO_VALIDATE_SIGNATURE=1.
"""
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.db import now_utc
from app.ivr import engine
from app.ivr.twilio_adapter import TwilioTransport, validate_signature
from app.models import Enrollment, FollowupCall

router = APIRouter(prefix="/webhooks/twilio", tags=["webhooks"])


async def _form(request: Request) -> dict[str, str]:
    body = await request.body()
    return {k: v[0] for k, v in parse_qs(body.decode()).items() if v}


def _check_sig(request: Request, body: bytes) -> None:
    if not validate_signature(request, body):
        raise HTTPException(403, "invalid twilio signature")


def _urls(call_id: str) -> tuple[str, str, str]:
    base = settings.PUBLIC_BASE_URL
    voice = f"{base}/webhooks/twilio/voice/{call_id}"
    gather = f"{base}/webhooks/twilio/gather/{call_id}"
    status = f"{base}/webhooks/twilio/status/{call_id}"
    return voice, gather, status


@router.post("/voice/{call_id}")
async def voice(call_id: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    _check_sig(request, body)
    call = db.query(FollowupCall).filter(FollowupCall.id == call_id).first()
    if not call:
        raise HTTPException(404)

    # verify-number special call (kind sentinel)
    if call.kind == "verify":
        t = TwilioTransport()
        t.play("verify_call")
        t.expect_digit("verify")
        _, gather, _ = _urls(call_id)
        return Response(content=t.render_twiml(gather + "?verify=1", gather + "?timeout=1"),
                        media_type="application/xml")

    t = TwilioTransport()
    if not call.current_node:
        engine.start_call(db, call_id, t)
    # if current_node is already a question (e.g. re-entry), the engine has already
    # recorded nothing; just re-expect by building a minimal gather from current node
    _, gather, _ = _urls(call_id)
    return Response(content=t.render_twiml(gather, gather + "?timeout=1"),
                    media_type="application/xml")


@router.post("/gather/{call_id}")
async def gather(call_id: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    _check_sig(request, body)
    call = db.query(FollowupCall).filter(FollowupCall.id == call_id).first()
    if not call:
        raise HTTPException(404)

    form = await _form(request)
    digit = form.get("Digits")
    is_verify = request.query_params.get("verify") == "1" or call.kind == "verify"

    _, gather_url, _ = _urls(call_id)

    # ── verify-number flow ───────────────────────────────────────────────────
    if is_verify:
        en = db.query(Enrollment).filter(Enrollment.id == call.enrollment_id).first()
        if digit == "1" and en is not None:
            en.number_verified = 1
            call.status = "completed"
            call.completed_at = now_utc()
            db.commit()
        t = TwilioTransport()
        t.hangup()
        return Response(content=t.render_twiml(gather_url, gather_url),
                        media_type="application/xml")

    # ── normal flow ──────────────────────────────────────────────────────────
    t = TwilioTransport()
    if digit:
        engine.handle_digit(db, call_id, digit, t)
    elif "timeout" in request.query_params:
        engine.handle_timeout(db, call_id, t)
    return Response(content=t.render_twiml(gather_url, gather_url + "?timeout=1"),
                    media_type="application/xml")


@router.post("/status/{call_id}")
async def status(call_id: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    _check_sig(request, body)
    call = db.query(FollowupCall).filter(FollowupCall.id == call_id).first()
    if not call:
        raise HTTPException(404)
    form = await _form(request)
    cs = form.get("CallStatus", "")
    # retry matrix (docs/04 §3.4)
    if cs in ("no-answer", "busy") and call.attempt < 2 and call.kind != "verify" and call.status in ("ringing", "in_progress"):
        from datetime import datetime, timedelta, timezone

        retry = FollowupCall(
            hospital_code=call.hospital_code,
            enrollment_id=call.enrollment_id,
            day_index=call.day_index,
            scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            attempt=call.attempt + 1, status="pending",
        )
        db.add(retry)
        call.status = "no_answer"
        db.commit()
    elif cs == "completed":
        # risk already persisted by engine.finish_call via gather flow;
        # only mark if somehow still in_progress (e.g. callee hung up mid-question)
        if call.status == "in_progress":
            call.status = "completed"
            call.completed_at = now_utc()
            db.commit()
    elif cs in ("failed", "canceled"):
        call.status = "failed"
        db.commit()
    return Response(status_code=204)
