"""Twilio transport + adapter (docs/04 §3).

The adapter is secret-INDEPENDENT code; only an actual ringing phone is the gate.
TwilioTransport is a *capturing* transport: it records play/expect/hangup calls and
renders them to TwiML — so the same engine (docs/04 §1) drives both Twilio
stateless webhooks and the WebSocket console (T16).
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.config import settings


def get_client() -> Client | None:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        return None
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def is_configured() -> bool:
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_FROM_NUMBER
        and settings.PUBLIC_BASE_URL
    )


def validate_signature(request: Request, body_bytes: bytes) -> bool:
    if not settings.TWILIO_VALIDATE_SIGNATURE:
        return True
    token = settings.TWILIO_AUTH_TOKEN
    sig = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)  # Starlette URL str includes the query string
    form: dict[str, str] = {}
    ctype = request.headers.get("content-type", "")
    if "form" in ctype:
        from urllib.parse import parse_qs

        form = {k: v[0] for k, v in parse_qs(body_bytes.decode()).items()}
    return RequestValidator(token).validate(url, form, sig)


# ── capturing transport ──────────────────────────────────────────────────────
class TwilioTransport:
    def __init__(self, next_timeout_url: str | None = None):
        self.actions: list[tuple] = []
        self._next_timeout_url = next_timeout_url

    def play(self, clip_id: str) -> None:
        self.actions.append(("play", clip_id))

    def expect_digit(self, node_id: str, options: dict | None = None,
                     timeout_s: int = 6) -> None:
        self.actions.append(("expect", node_id))

    def hangup(self) -> None:
        self.actions.append(("hangup", None))

    def render_twiml(self, gather_action_url: str, timeout_action_url: str) -> str:
        """Render recorded actions into one TwiML response for the next turn."""
        vr = VoiceResponse()
        # group trailing plays before an expect into a <Gather>
        plays: list[str] = []
        expect: str | None = None
        hungup = False
        for kind, val in self.actions:
            if kind == "play":
                plays.append(val)
            elif kind == "expect":
                expect = val
            elif kind == "hangup":
                hungup = True

        if expect is not None:
            g = Gather(num_digits=1, timeout=6, action=gather_action_url, method="POST")
            for c in plays:
                g.play(url=f"{settings.PUBLIC_BASE_URL}/audio/{c}.mp3")
            vr.append(g)
            vr.redirect(timeout_action_url, method="POST")
        else:
            for c in plays:
                vr.play(url=f"{settings.PUBLIC_BASE_URL}/audio/{c}.mp3")
            if hungup:
                vr.hangup()
        return str(vr)


def place_call(
    *,
    call_id: str,
    to_number: str,
    voice_url: str,
    status_callback: str,
) -> str:
    """Place an outbound call. Refuses numbers outside CALL_ALLOWLIST (docs/02 §7.7)."""
    if not is_configured():
        raise RuntimeError("twilio not configured (sid/token/from/public_url)")
    if to_number not in settings.call_allowlist_set:
        raise PermissionError(f"number not in CALL_ALLOWLIST: {to_number}")
    c = get_client()
    assert c is not None  # is_configured() ensured above
    call = c.calls.create(
        to=to_number,
        from_=settings.TWILIO_FROM_NUMBER,
        url=voice_url,
        status_callback=status_callback,
        status_callback_event=["completed", "no-answer", "busy", "failed"],
        timeout=30,
    )
    return call.sid


__all__ = [
    "TwilioTransport", "get_client", "is_configured", "validate_signature",
    "place_call",
]