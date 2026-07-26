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
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── login rate limiter (docs/02 §7.3) ────────────────────────────────────────
_lock = threading.Lock()
_attempts: dict[tuple[str, str], dict] = {}
RATE_LIMIT = 5
LOCK_SECONDS = 15 * 60
_SWEEP_INTERVAL = 300  # purge stale entries every 5 min


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
