"""Groq API key rotation (free-tier friendly).

The Groq free tier has per-key rate limits (currently ~30 requests/min for
`llama-3.3-70b-versatile`). With multiple free keys you can multiply that
limit. The rotator round-robins on success and skips keys that 429.

Config
------
- `GROQ_API_KEY`     legacy single key (kept for backwards compat) — becomes key #0
- `GROQ_API_KEYS`    comma-separated list of additional keys
                      (e.g. `gsk_a,gsk_b,gsk_c`)

Behaviour
---------
- `llm_enabled()` is True if ≥1 key is configured.
- Every Groq POST goes through `GroqKeyRotator.call()` which picks the
  next available key, runs the request, and on HTTP 429 marks the key as
  cooldown for 60 s before rotating.
- If every key is on cooldown, the rotator raises `AllKeysExhausted` and
  the caller (e.g. `suggest_protocol`) falls back to its deterministic
  template (existing behaviour — never blocks the request flow).

State
-----
Process-local (an in-memory dict). For multi-worker / multi-replica deploys
move the cooldown map to Redis. The `Dockerfile` uses `--workers 1` to keep
this simple on the free tier; see FREE_DEPLOY.md for the scale path.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("llm.rotator")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

COOLDOWN_SECONDS = 60.0
SWEEP_INTERVAL = 30.0


class AllKeysExhausted(Exception):
    """Raised when every key is currently rate-limited or failing."""


class GroqKeyRotator:
    """Round-robin with 429-cooldown. Thread-safe."""

    def __init__(self, keys: list[str], cooldown_seconds: float = COOLDOWN_SECONDS):
        if not keys:
            raise ValueError("at least one key is required")
        self._keys = list(keys)
        self._idx = 0
        self._cooldown_until: dict[str, float] = {}
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def add_key(self, key: str) -> None:
        if key and key not in self._keys:
            with self._lock:
                self._keys.append(key)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < SWEEP_INTERVAL:
            return
        self._last_sweep = now
        expired = [k for k, t in self._cooldown_until.items() if t <= now]
        for k in expired:
            self._cooldown_until.pop(k, None)

    def _is_available(self, key: str, now: float) -> bool:
        until = self._cooldown_until.get(key, 0.0)
        return until <= now

    def _next_available(self, now: float) -> str | None:
        """Return the next key whose cooldown has expired, or None if all
        keys are on cooldown. Round-robins from the current index."""
        n = len(self._keys)
        for offset in range(n):
            k = self._keys[(self._idx + offset) % n]
            if self._is_available(k, now):
                self._idx = (self._idx + offset + 1) % n
                return k
        return None

    def _cooldown(self, key: str, now: float, seconds: float | None = None) -> None:
        self._cooldown_until[key] = now + (seconds or self._cooldown_seconds)

    def call(
        self,
        payload: dict[str, Any],
        timeout: float = 4.0,
    ) -> httpx.Response:
        """Send a chat-completions request. Round-robins on success, skips
        rate-limited keys. Raises AllKeysExhausted if every key is on
        cooldown or every request 4xx/5xx-es."""
        last_error: Exception | None = None
        attempts = 0
        max_attempts = len(self._keys)
        while attempts < max_attempts:
            now = time.time()
            with self._lock:
                self._sweep(now)
                key = self._next_available(now)
            if key is None:
                # every key on cooldown → give up
                break
            try:
                r = httpx.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=timeout,
                )
            except Exception as ex:
                last_error = ex
                log.warning("groq key %s… network error: %s", key[:8], ex)
                # short cooldown for transient network errors
                with self._lock:
                    self._cooldown(key, time.time(), seconds=10)
                attempts += 1
                continue

            if r.status_code == 429:
                # honour Retry-After if the server sent one
                retry_after = r.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else self._cooldown_seconds
                except (TypeError, ValueError):
                    wait = self._cooldown_seconds
                with self._lock:
                    self._cooldown(key, time.time(), seconds=wait)
                log.info("groq key %s… 429, cooldown %.0fs", key[:8], wait)
                attempts += 1
                continue

            if r.status_code >= 500:
                # server error — short cooldown, try the next key
                with self._lock:
                    self._cooldown(key, time.time(), seconds=15)
                log.warning("groq key %s… server error %s", key[:8], r.status_code)
                attempts += 1
                continue

            # 2xx and 4xx (other than 429) — return as-is. 4xx is a
            # caller error (e.g. bad payload) and won't be fixed by a
            # different key.
            return r

        if last_error is not None:
            raise AllKeysExhausted(str(last_error))
        raise AllKeysExhausted("all groq keys are on cooldown")


# ── module-level singleton, built once from settings ─────────────────────────

def _all_keys() -> list[str]:
    keys: list[str] = []
    if settings.GROQ_API_KEY:
        keys.append(settings.GROQ_API_KEY)
    if settings.GROQ_API_KEYS:
        for k in settings.GROQ_API_KEYS.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    return keys


_rotator: GroqKeyRotator | None = None
_rotator_lock = threading.Lock()


def get_rotator() -> GroqKeyRotator | None:
    """Lazy-init the module-level rotator from the current settings.
    Returns None if no keys are configured (callers should fall back)."""
    global _rotator
    keys = _all_keys()
    if not keys:
        return None
    with _rotator_lock:
        if _rotator is None or _rotator.keys != keys:
            _rotator = GroqKeyRotator(keys)
        return _rotator


def reset_rotator() -> None:
    """For tests: forget the cached rotator so a new one is built from
    the current settings."""
    global _rotator
    with _rotator_lock:
        _rotator = None
