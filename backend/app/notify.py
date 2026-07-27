"""Telegram red-flag alerts (docs/04 §6). Plain httpx, no SDK.

Fires on escalation creation only (engine red hook). Failures are swallowed so a
Telegram outage never breaks a call. No-op when TELEGRAM_BOT_TOKEN is unset.

Also exposes telegram_send() for one-off messages (T10 forgot-password OTP, T12
pending-notification retry). No emoji per team rule.
"""
import logging

import httpx

from app.config import settings
from app.models import Enrollment, Patient

log = logging.getLogger("notify")


def _mask(phone: str) -> str:
    if len(phone) <= 4:
        return "••••"
    return f"{phone[:6]}•••••{phone[-3:]}"


def _message(esc, patient_name: str, phone: str) -> str:
    reasons = esc.reasons or "[]"
    # Strip the surrounding brackets the engine stores for JSON safety.
    if reasons.startswith("[") and reasons.endswith("]"):
        reasons = reasons[1:-1]
    return (
        f"RED FLAG — {settings.HOSPITAL_NAME}\n"
        f"Patient: {patient_name} (enrollment {esc.enrollment_id[:8]})\n"
        f"Reasons: {reasons}\n"
        f"Caregiver: {_mask(phone)}\n"
        f"Escalation page: {settings.PUBLIC_BASE_URL}/escalations\n"
        f"Call caregiver: tel:{phone}"
    )


def telegram_red(esc) -> None:
    """Synchronous (called from the engine hook, on a worker thread).

    T12: also writes a row to pending_notifications. A scheduler job
    (`retry_pending_notifications` in app.scheduler) retries failed sends
    every 5 min for up to 5 attempts, then marks `failed` and publishes an
    SSE `notification:failed` event so the dashboard can surface it.
    """
    from app.db import SessionLocal
    from datetime import datetime, timezone, timedelta
    from app.models import PendingNotification

    s = SessionLocal()
    try:
        en = s.query(Enrollment).filter(Enrollment.id == esc.enrollment_id).first()
        if not en:
            return None
        p = s.query(Patient).filter(Patient.id == en.patient_id).first()
        if not p:
            return None
        msg = _message(esc, p.name, p.caregiver_phone)
    finally:
        s.close()

    # L2: also fire the external webhook (HMAC-signed). Failures are
    # swallowed — the escalation flow is not blocked.
    try:
        webhook_send(esc, p.name, p.caregiver_phone,
                     settings.HOSPITAL_CODE, en.ward, en.protocol_id)
    except Exception as ex:
        log.warning("webhook_send raised: %s", ex)

    # Primary Telegram send
    sent = _send(msg)
    # DB row: status='sent' OR status='pending' (for the retry job)
    now_iso = datetime.now(timezone.utc).isoformat()
    s = SessionLocal()
    try:
        row = PendingNotification(
            hospital_code=settings.HOSPITAL_CODE,
            kind="escalation",
            entity_id=esc.id,
            text=msg,
            attempt=1,
            last_error=None if sent else "telegram send failed",
            next_retry_at=now_iso if sent else
                          (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            status="sent" if sent else "pending",
            sent_at=now_iso if sent else None,
        )
        s.add(row); s.commit()
    except Exception as ex:
        log.warning("pending_notifications insert failed: %s", ex)
        s.rollback()
    finally:
        s.close()
    if not sent:
        log.warning("telegram_red: primary send failed for escalation %s; queued for retry", esc.id)
    return None


def telegram_send(text: str) -> bool:
    """Send a free-text message to the configured Telegram chat. Returns True on
    2xx, False otherwise. Failures are logged + swallowed — never block callers.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.info("telegram disabled (no token/chat) — message dropped (len=%d)", len(text))
        return False
    return _send(text)


# ── L2: escalation webhook (HMAC-signed) ──────────────────────────────────────
def webhook_send(esc, patient_name: str, phone: str,
                hospital_code: str, ward: str | None,
                protocol_id: str) -> bool:
    """Fire a signed webhook to ESCALATION_WEBHOOK_URL. Returns True on 2xx.

    Body is JSON: {event, hospital_code, escalation_id, patient_name, level,
    reasons, ward, protocol_id, phone, timestamp}. Signed with HMAC-SHA256
    over the body using ESCALATION_WEBHOOK_SECRET. Signature in
    `X-Signature: sha256=<hex>` header.

    Failure modes (timeout, 5xx, network) are logged + swallowed. The
    escalation flow is not blocked by a webhook outage.
    """
    if not settings.ESCALATION_WEBHOOK_URL:
        return False
    import hashlib
    import hmac
    import json as _json
    from datetime import datetime, timezone
    payload = {
        "event": "escalation",
        "hospital_code": hospital_code,
        "escalation_id": esc.id,
        "patient_name": patient_name,
        "level": esc.level,
        "reasons": _json.loads(esc.reasons or "[]"),
        "ward": ward,
        "protocol_id": protocol_id,
        "phone": phone,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    body = _json.dumps(payload, sort_keys=True).encode()
    sig = hmac.new(
        settings.ESCALATION_WEBHOOK_SECRET.encode(),
        body, hashlib.sha256,
    ).hexdigest()
    try:
        r = httpx.post(
            settings.ESCALATION_WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json",
                     "X-Signature": f"sha256={sig}"},
            timeout=5.0,
        )
        if r.status_code >= 300:
            log.warning("escalation webhook %s: %s", r.status_code, r.text[:200])
        return r.status_code < 300
    except Exception as ex:
        log.warning("escalation webhook failed: %s", ex)
        return False


def _send(text: str) -> bool:
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": settings.TELEGRAM_CHAT_ID,
                  "text": text, "disable_web_page_preview": True},
            timeout=5.0,
        )
        if r.status_code != 200:
            log.warning("telegram sendMessage %s: %s", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as ex:
        log.warning("telegram failed: %s", ex)
        return False
