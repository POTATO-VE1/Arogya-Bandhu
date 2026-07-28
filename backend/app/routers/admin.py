"""L2 admin router — escalation webhook test endpoint.

`routers/webhooks.py` is for Twilio inbound. Admin-only endpoints
(webhook test, future admin ops) live here.
"""
import json
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import require_admin
from app.ivr import twilio_rotator
from app.models import User
from app.notify import webhook_send

router = APIRouter(prefix="/api/admin", tags=["admin"])


class TestWebhookIn(BaseModel):
    patient_name: str = Field(default="Test Patient", max_length=200)
    level: str = Field(default="red", pattern="^(red|yellow|green)$")
    ward: str | None = Field(default="Surgical", max_length=100)
    protocol_id: str = Field(default="wound_care", max_length=50)
    reasons: List[str] = Field(default_factory=lambda: ["smoke test"])


@router.post("/webhooks/test")
def test_webhook(body: TestWebhookIn, user: User = Depends(require_admin)):
    """Send a test payload to ESCALATION_WEBHOOK_URL and report the result.

    Used by the demo: open a webhook.site URL, point ESCALATION_WEBHOOK_URL
    at it, hit this endpoint, and watch the test payload arrive with
    `X-Signature: sha256=...` in the headers.
    """
    if not settings.ESCALATION_WEBHOOK_URL:
        raise HTTPException(400, "ESCALATION_WEBHOOK_URL not configured")
    # Use a fake escalation id (real one not needed for the smoke test)
    from app.models import Escalation
    fake = Escalation(
        id="test-fake-id",
        level=body.level,
        reasons=json.dumps(body.reasons),
    )
    ok = webhook_send(
        fake, body.patient_name, "+91XXXXXXXXXX",
        settings.HOSPITAL_CODE, body.ward, body.protocol_id,
    )
    return {
        "ok": ok,
        "url": settings.ESCALATION_WEBHOOK_URL,
        "payload": {
            "event": "escalation",
            "escalation_id": "test-fake-id",
            "patient_name": body.patient_name,
            "level": body.level,
            "reasons": body.reasons,
            "ward": body.ward,
            "protocol_id": body.protocol_id,
        },
    }


@router.get("/twilio-health")
def twilio_health(user: User = Depends(require_admin)):
    """Live status of every configured Twilio account.

    Returns per-account:
      - name, from_number, allowlist size
      - state: 'ok' | 'cooldown' | 'unavailable'
      - cooldown_remaining_s (if on cooldown)
      - last_seen (epoch seconds; 0 if never used)
      - fail_count

    Plus a top-level `rotator_configured` boolean so the frontend can show
    a clear 'Twilio is not configured' state on the dashboard.
    """
    rot = twilio_rotator.get_rotator()
    if rot is None:
        return {
            "rotator_configured": False,
            "message": "No Twilio accounts configured. Set TWILIO_ACCOUNTS or "
                       "TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER env vars.",
            "accounts": [],
            "global_allowlist": list(settings.call_allowlist_set),
        }
    now = time.time()
    accounts = []
    for acc in rot.accounts:
        cd_until = rot._cooldown_until.get(acc.name, 0.0)
        cooldown_left = max(0.0, cd_until - now) if cd_until > now else 0.0
        if acc.unavailable:
            state = "unavailable"
        elif cooldown_left > 0:
            state = "cooldown"
        else:
            state = "ok"
        accounts.append({
            "name": acc.name,
            "from_number": acc.from_number,
            "allowlist_count": len(acc.allowlist),
            "state": state,
            "cooldown_remaining_s": round(cooldown_left, 1),
            "last_seen": acc.last_seen,
            "fail_count": acc.fail_count,
        })
    return {
        "rotator_configured": True,
        "accounts": accounts,
        "global_allowlist_size": len(rot.global_allowlist),
        "public_base_url": settings.PUBLIC_BASE_URL or "",
    }


class FailoverTestIn(BaseModel):
    """Force a temporary cooldown on one account, then place a real call
    through the rotator. Used by the demo to show the path
    'account-1 on cooldown → rotator picks account-2'."""
    fail_account: str = Field(
        default="primary", description="Account name to put on cooldown")
    cooldown_seconds: int = Field(default=30, ge=5, le=120)
    to_number: str | None = Field(
        default=None, description="E.164 number to dial; if empty, picks one "
        "from the global allowlist")


@router.post("/twilio-failover-test")
def twilio_failover_test(body: FailoverTestIn, user: User = Depends(require_admin)):
    """Demo path: force one account into cooldown, place a call. The response
    shows which accounts were tried, in what order, and which one succeeded.

    This is admin-only. Safe: cooldown is at most 120s and the rotator
    recovers automatically.
    """
    import threading
    from app.ivr.twilio_rotator import NoAccountAvailable
    rot = twilio_rotator.get_rotator()
    if rot is None:
        raise HTTPException(503, "Twilio not configured")

    target = (body.to_number or "").strip() or None
    if not target:
        # pick the first number that any non-failing account has
        for acc in rot.accounts:
            if acc.allowlist and not acc.unavailable:
                target = sorted(acc.allowlist)[0]
                break
    if not target:
        raise HTTPException(400, "No diallable number in any allowlist")

    # Force-fail the chosen account
    failed_account = None
    for acc in rot.accounts:
        if acc.name == body.fail_account:
            with rot._lock:
                rot._cooldown(acc, time.time(), seconds=body.cooldown_seconds)
            failed_account = acc.name
            break
    if failed_account is None:
        raise HTTPException(404, f"Account '{body.fail_account}' not configured")

    # Find an available call to use as a vehicle (we just need a call_id
    # to place a real Twilio call). Use the first pending call, else
    # create a synthetic one.
    from app.db import SessionLocal
    from app.models import Enrollment, FollowupCall
    from app.ivr import engine
    s = SessionLocal()
    try:
        e = s.query(Enrollment).filter(
            Enrollment.status == "active",
        ).order_by(Enrollment.created_at.desc()).first()
        if not e:
            raise HTTPException(503, "No active enrollment to attach a demo call to")
        c = FollowupCall(
            hospital_code=e.hospital_code, enrollment_id=e.id,
            day_index=0, scheduled_at=__import__("datetime").datetime.utcnow().isoformat(),
            kind="demo", status="ringing", triggered_by=user.id,
        )
        s.add(c); s.commit(); s.refresh(c)
        call_id = c.id
    finally:
        s.close()

    base = settings.PUBLIC_BASE_URL
    if not base:
        raise HTTPException(503, "PUBLIC_BASE_URL not set; cannot build webhooks")
    voice = f"{base}/webhooks/twilio/voice/{call_id}"
    status_cb = f"{base}/webhooks/twilio/status/{call_id}"

    # Try the call. The rotator will SKIP the failed account and try the next.
    try:
        from app.ivr.twilio_adapter import place_call as adapter_place_call
        # The adapter sets a hardcoded _urls() that may differ; use direct
        from app.ivr import twilio_adapter
        sid, account_name = twilio_adapter.place_call(
            call_id=call_id, to_number=target,
            voice_url=voice, status_callback=status_cb,
        )
    except NoAccountAvailable as ex:
        return {
            "ok": False,
            "target": target,
            "failed_account": failed_account,
            "cooldown_seconds": body.cooldown_seconds,
            "tried": [{"name": a.name, "from": a.from_number,
                       "state": "unavailable" if a.unavailable else "ok"}
                      for a in rot.accounts],
            "winner": None,
            "error": str(ex),
        }

    return {
        "ok": True,
        "target": target,
        "failed_account": failed_account,
        "cooldown_seconds": body.cooldown_seconds,
        "tried": [{"name": a.name, "from": a.from_number,
                   "state": "unavailable" if a.unavailable else "ok"}
                  for a in rot.accounts],
        "winner": {"name": account_name, "call_sid": sid},
    }

