"""Google Fit OAuth2 flow — authorize, token exchange, refresh.

No SDK dependencies — pure httpx + stdlib.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx

from app.config import settings

log = logging.getLogger("health_fit.oauth")

# ── Google OAuth2 endpoints ───────────────────────────────────────────────────
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# ── Scopes we request from Google Fit ────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.oxygen_saturation.read",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.body.read",
]

# ── PKCE support (Google recommends it for web apps) ─────────────────────────


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# In-memory PKCE store (per state token → code_verifier)
_pkce_store: dict[str, str] = {}


def get_authorize_url(telegram_id: int, redirect_uri: str) -> str:
    """Build the Google OAuth2 authorization URL with PKCE."""
    if not settings.GOOGLE_FIT_CLIENT_ID:
        raise ValueError("GOOGLE_FIT_CLIENT_ID not configured")

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    # Store PKCE verifier keyed by state
    _pkce_store[state] = code_verifier

    params = {
        "client_id": settings.GOOGLE_FIT_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": f"{telegram_id}:{state}",
        "access_type": "offline",       # get refresh_token
        "prompt": "consent",            # force consent to always get refresh_token
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, state: str, redirect_uri: str) -> dict | None:
    """Exchange authorization code for tokens. Returns token dict or None on failure.

    Returns: {
        "access_token": str,
        "refresh_token": str,
        "expires_in": int,
        "scope": str,
        "token_type": str,
    }
    """
    if not settings.GOOGLE_FIT_CLIENT_ID or not settings.GOOGLE_FIT_CLIENT_SECRET:
        log.warning("Google Fit OAuth not configured")
        return None

    # Retrieve PKCE verifier
    code_verifier = _pkce_store.pop(state, None)

    data = {
        "client_id": settings.GOOGLE_FIT_CLIENT_ID,
        "client_secret": settings.GOOGLE_FIT_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    try:
        r = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=10.0)
        if r.status_code == 200:
            return r.json()
        log.warning("token exchange failed %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("token exchange error: %s", e)
    return None


def refresh_access_token(refresh_token_encrypted: str, fernet: "Fernet") -> dict | None:
    """Refresh an expired access token using the stored refresh token.

    Returns: {"access_token": str, "expires_in": int} or None on failure.
    """
    if not settings.GOOGLE_FIT_CLIENT_ID or not settings.GOOGLE_FIT_CLIENT_SECRET:
        return None

    try:
        refresh_token = fernet.decrypt(refresh_token_encrypted.encode()).decode()
    except Exception:
        log.warning("failed to decrypt refresh token")
        return None

    data = {
        "client_id": settings.GOOGLE_FIT_CLIENT_ID,
        "client_secret": settings.GOOGLE_FIT_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        r = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=10.0)
        if r.status_code == 200:
            return r.json()
        log.warning("token refresh failed %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("token refresh error: %s", e)
    return None
