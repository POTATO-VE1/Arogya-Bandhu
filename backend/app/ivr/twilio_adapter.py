"""Twilio transport + adapter (docs/04 §3).

The adapter is secret-INDEPENDENT code; only an actual ringing phone is the gate.
TwilioTransport is a *capturing* transport: it records play/expect/hangup calls and
renders them to TwiML — so the same engine (docs/04 §1) drives both Twilio
stateless webhooks and the WebSocket console (T16).

Multi-account support (free-tier friendly)
------------------------------------------
Calls are placed via `TwilioAccountRotator` (see `twilio_rotator.py`).
Configure one or more Twilio accounts via the `TWILIO_ACCOUNTS` JSON env
var, or use the legacy `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` +
`TWILIO_FROM_NUMBER` + `CALL_ALLOWLIST` for a single account. Each account
can have up to 5 verified caller IDs in its `allowlist`; numbers in the
union of all allowlists are diallable. The rotator round-robins on
success and skips accounts that 429 / 5xx.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Gather

from app.config import settings
from app.ivr import twilio_rotator
from app.ivr.twilio_rotator import NoAccountAvailable


# ── webhook signature cache (60s TTL) ────────────────────────────────────────
# Twilio retries webhooks on 5xx; if the validator is slow, a retry storm
# can pile up. With 2-3 accounts × RequestValidator per call, a cache
# drops the per-webhook CPU from O(accounts) to O(1).
_sig_cache: dict[tuple, tuple[bool, float]] = {}
_sig_cache_lock = threading.Lock()
SIG_CACHE_TTL = 60.0
SIG_CACHE_MAX = 1000  # bound to prevent unbounded growth


def _sig_cache_get(key: tuple) -> bool | None:
    now = time.time()
    with _sig_cache_lock:
        v = _sig_cache.get(key)
        if v is None:
            return None
        result, ts = v
        if now - ts > SIG_CACHE_TTL:
            _sig_cache.pop(key, None)
            return None
        return result


def _sig_cache_set(key: tuple, result: bool) -> None:
    now = time.time()
    with _sig_cache_lock:
        if len(_sig_cache) >= SIG_CACHE_MAX:
            # drop the oldest entry by expiring everything older than TTL
            expired = [k for k, (_, ts) in _sig_cache.items()
                       if now - ts > SIG_CACHE_TTL]
            for k in expired:
                _sig_cache.pop(k, None)
            # if still over, drop ~10% of the rest (FIFO)
            if len(_sig_cache) >= SIG_CACHE_MAX:
                keys = list(_sig_cache.keys())[:SIG_CACHE_MAX // 10]
                for k in keys:
                    _sig_cache.pop(k, None)
        _sig_cache[key] = (result, now)


def is_configured() -> bool:
    """True if at least one Twilio account is configured AND we have a
    public URL to build webhook callbacks."""
    return (twilio_rotator.get_rotator() is not None
            and bool(settings.PUBLIC_BASE_URL))


def validate_signature(request: Request, body_bytes: bytes) -> bool:
    """Validate against every configured account's auth token. Twilio
    signs the request with the auth-token of the account that placed
    the call, so we have to try each one until one matches.

    Cached for 60s by (url, body) to absorb Twilio retry storms.
    """
    if not settings.TWILIO_VALIDATE_SIGNATURE:
        return True
    cache_key = (str(request.url), body_bytes)
    cached = _sig_cache_get(cache_key)
    if cached is not None:
        return cached

    rotator = twilio_rotator.get_rotator()
    if rotator is None:
        # No accounts configured; can't validate. Reject.
        _sig_cache_set(cache_key, False)
        return False
    sig = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    form: dict[str, str] = {}
    ctype = request.headers.get("content-type", "")
    if "form" in ctype:
        from urllib.parse import parse_qs
        form = {k: v[0] for k, v in parse_qs(body_bytes.decode()).items()}
    result = False
    for acc in rotator.accounts:
        try:
            if RequestValidator(acc.token).validate(url, form, sig):
                result = True
                break
        except Exception:
            continue
    _sig_cache_set(cache_key, result)
    return result


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
) -> tuple[str, str]:
    """Place an outbound call. Returns (call_sid, account_name).

    Multi-account behaviour: the rotator picks an account that has
    `to_number` verified, tries to place, and rotates to the next
    eligible account on rate-limit / capacity error.
    """
    rotator = twilio_rotator.get_rotator()
    if rotator is None:
        raise RuntimeError("twilio not configured (no accounts in TWILIO_ACCOUNTS / single-account env vars)")
    if not settings.PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL is required for Twilio webhooks")
    try:
        return rotator.place_call(
            call_id=call_id, to_number=to_number,
            voice_url=voice_url, status_callback=status_callback,
        )
    except NoAccountAvailable as ex:
        raise PermissionError(f"number not in any account's allowlist: {to_number} ({ex})") from ex


# Backwards-compat alias for the legacy `get_client()`. Not used by the
# rotator path; left so old code that imported it doesn't break.
def get_client():
    from twilio.rest import Client
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        return None
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


__all__ = [
    "TwilioTransport", "get_client", "is_configured", "validate_signature",
    "place_call",
]
