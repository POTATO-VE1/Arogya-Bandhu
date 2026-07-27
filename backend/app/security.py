import hashlib
import hmac
import os
import threading
import time

ITERATIONS = 600_000  # OWASP pbkdf2-sha256 recommendation


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
    except (ValueError, AttributeError):
        return False
    try:
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── login rate limiter (docs/02 §7.3) ────────────────────────────────────────
_lock = threading.Lock()
_attempts: dict[tuple[str, str], dict] = {}
RATE_LIMIT = 5
# Production-grade: 15 min lockout (OWASP). Dev: 30s for faster iteration.
LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "30"))
_SWEEP_INTERVAL = 300  # purge stale entries every 5 min


def reset_rate_limits() -> None:
    """Clear all active rate limits."""
    with _lock:
        _attempts.clear()


def _sweep(now: float) -> None:
    """Remove entries whose lock has expired to prevent unbounded growth."""
    expired = [k for k, v in _attempts.items()
               if now >= v["locked_until"] and v["count"] >= RATE_LIMIT]
    for k in expired:
        _attempts.pop(k, None)


_last_sweep = [0.0]


def check_rate(ip: str, username: str) -> bool:
    now = time.time()
    with _lock:
        if now - _last_sweep[0] > _SWEEP_INTERVAL:
            _sweep(now)
            _last_sweep[0] = now
        rec = _attempts.get((ip, username))
        if not rec:
            return True
        if now < rec["locked_until"]:
            return False
        return True  # lock expired → allow, count cleared on next failure


def record_failure(ip: str, username: str) -> None:
    now = time.time()
    with _lock:
        rec = _attempts.setdefault((ip, username), {"count": 0, "locked_until": 0.0})
        rec["count"] += 1
        if rec["count"] >= RATE_LIMIT:
            rec["locked_until"] = now + LOCK_SECONDS
            rec["count"] = 0


def record_success(ip: str, username: str) -> None:
    with _lock:
        _attempts.pop((ip, username), None)


# ── generic IP-based rate limiter (Phase 1) ─────────────────────────────
# Used for endpoints that don't have a per-username key (search,
# forgot password, etc.). Cap entries + sweep to prevent unbounded
# growth in long-running processes.

_ip_lock = threading.Lock()
_ip_records: dict[str, dict] = {}  # ip -> {count, locked_until, last_seen}
IP_RATE_LIMIT = 30
IP_LOCK_SECONDS = 60
IP_SWEEP_INTERVAL = 300
_last_ip_sweep = [0.0]


def _evict_ip_records() -> None:
    if len(_ip_records) <= 5000:
        return
    by_seen = sorted(_ip_records.items(), key=lambda kv: kv[1].get("last_seen", 0))
    for ip, _ in by_seen[: 500]:
        _ip_records.pop(ip, None)


def check_ip_rate(ip: str) -> bool:
    now = time.time()
    with _ip_lock:
        if now - _last_ip_sweep[0] > IP_SWEEP_INTERVAL:
            _evict_ip_records()
            _last_ip_sweep[0] = now
        rec = _ip_records.get(ip)
        if not rec:
            return True
        if now < rec["locked_until"]:
            return False
        # Sliding window — reset count after 60s of quiet
        if now - rec.get("last_seen", 0) > 60:
            rec["count"] = 0
        return rec.get("count", 0) < IP_RATE_LIMIT


def record_ip_hit(ip: str) -> None:
    now = time.time()
    with _ip_lock:
        rec = _ip_records.setdefault(ip, {"count": 0, "locked_until": 0.0, "last_seen": now})
        rec["last_seen"] = now
        rec["count"] = rec.get("count", 0) + 1
        if rec["count"] >= IP_RATE_LIMIT:
            rec["locked_until"] = now + IP_LOCK_SECONDS


def reset_ip_rate_limits() -> None:
    with _ip_lock:
        _ip_records.clear()
