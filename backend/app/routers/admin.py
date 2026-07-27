"""L2 admin router — escalation webhook test endpoint.

`routers/webhooks.py` is for Twilio inbound. Admin-only endpoints
(webhook test, future admin ops) live here.
"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import require_admin
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
