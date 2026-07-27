"""Input security guards for Telegram messages."""
from __future__ import annotations

import re
from typing import Optional

# Max input length (Telegram text messages)
MAX_INPUT_LEN = 500

# Injection patterns to reject
_INJECTION_PATTERNS = [
    re.compile(r"(?:^|\s)(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|above|prior)", re.I),
    re.compile(r"(?:^|\s)(?:system|admin|root)\s*:", re.I),
    re.compile(r"<script", re.I),
    re.compile(r"\{\{.*\}\}"),
    re.compile(r"__\w+__"),
]

# Rate limit: max messages per user per minute
_RATE_LIMIT = 20


class InputRejected(Exception):
    """Raised when input fails security checks."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class RateLimiter:
    """Simple in-memory sliding window rate limiter per user."""

    def __init__(self, max_per_minute: int = _RATE_LIMIT):
        self.max = max_per_minute
        self._windows: dict[str, list[float]] = {}

    def check(self, user_id: str) -> None:
        import time
        now = time.time()
        window = self._windows.setdefault(user_id, [])
        # prune entries older than 60s
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= self.max:
            raise InputRejected("rate_limit")
        window.append(now)


_limiter = RateLimiter()


def validate_input(text: str, user_id: str) -> str:
    """Validate and sanitise a Telegram message. Returns cleaned text or raises InputRejected."""
    if not text or not text.strip():
        raise InputRejected("empty")

    text = text.strip()

    if len(text) > MAX_INPUT_LEN:
        raise InputRejected("too_long")

    for pat in _INJECTION_PATTERNS:
        if pat.search(text):
            raise InputRejected("injection")

    _limiter.check(user_id)

    return text


def normalise_digit(raw: str) -> Optional[str]:
    """Extract a single digit from a Telegram message (supports Kannada numerals)."""
    kannada = {"೦": "0", "೧": "1", "೨": "2", "೩": "3", "೪": "4",
               "೫": "5", "೬": "6", "೭": "7", "೮": "8", "೯": "9"}
    for ch in raw:
        if ch.isdigit():
            return ch
        if ch in kannada:
            return kannada[ch]
    return None
