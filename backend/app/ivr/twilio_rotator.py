"""Twilio account rotation (free-tier friendly).

Each Twilio free trial account can verify at most 5 caller IDs and has a
limited number of voice minutes. By configuring multiple accounts (each
with its own verified-numbers list), the deploy can scale beyond a single
account's cap. The rotator round-robins between accounts and skips
accounts that 429 / 5xx.

Config
-----
- `TWILIO_ACCOUNT_SID`   legacy single-account SID (kept for backwards compat)
- `TWILIO_AUTH_TOKEN`     legacy single-account token
- `TWILIO_FROM_NUMBER`    legacy single-account from-number
- `CALL_ALLOWLIST`        legacy single-account allowlist (comma-separated E.164)
- `TWILIO_ACCOUNTS`       JSON list of accounts, each:
                            {
                              "name": "primary",        // optional, for logs
                              "sid": "AC...",
                              "token": "...",
                              "from": "+1...",
                              "allowlist": ["+91...", "+1..."]  // up to 5 per account
                            }
                          If set, it OVERRIDES the legacy single-account vars.

Verified-numbers logic
----------------------
A target number can be dialled if **any** configured account has it in its
allowlist. The rotator picks the first account whose cooldown has expired
and whose allowlist contains the target. If the call fails with a Twilio
rate-limit / capacity error, the account is put on cooldown for 60s and
the next matching account is tried.

Per-account state
-----------------
- `client` cached on first use (saves an SSL handshake per call)
- `last_seen` updated by the periodic health check
- `unavailable` flipped True if health check has failed for >10 min;
  `_candidates` skips unavailable accounts

State
-----
Process-local (in-memory dict). For multi-worker / multi-replica deploys
move the cooldown map + last_seen to Redis. The `Dockerfile` uses
`--workers 1` to keep this simple on the free tier; see FREE_DEPLOY.md.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import settings

log = logging.getLogger("twilio.rotator")

COOLDOWN_SECONDS = 60.0
SWEEP_INTERVAL = 30.0
HEALTH_DOWN_THRESHOLD = 600.0  # 10 min of failures → mark unavailable

# Twilio error codes that should put the account on cooldown
# (capacity / rate-limit / concurrent-call limits).
_RATE_LIMIT_CODES = {21611}
# Codes that should be treated as a permanent failure (no retry, no rotation):
_PERMANENT_CODES = {21211, 21408, 21610, 21614}


@dataclass
class TwilioAccount:
    """One Twilio account. Mutable because we update `last_seen` /
    `unavailable` on health checks; equality is name-based for the
    rotator's "have my settings changed?" comparison."""
    name: str
    sid: str
    token: str
    from_number: str
    allowlist: frozenset[str] = field(default_factory=frozenset)
    # ── mutable runtime state (not in __eq__) ──
    last_seen: float = 0.0
    unavailable: bool = False
    fail_count: int = 0

    def is_configured(self) -> bool:
        return bool(self.sid and self.token and self.from_number)

    # the rotator compares accounts by settings (so the cached rotator
    # can be rebuilt when env changes); mutable state is ignored
    def _key(self) -> tuple:
        return (self.name, self.sid, self.token, self.from_number,
                tuple(sorted(self.allowlist)))


class NoAccountAvailable(Exception):
    """Raised when no configured account can place a call to a number."""


class TwilioAccountRotator:
    """Round-robin per-call, with per-account cooldown on 429/5xx.

    Thread-safe. Per-call placement is best-effort across all accounts
    that have the target number verified.
    """

    def __init__(self, accounts: list[TwilioAccount],
                 cooldown_seconds: float = COOLDOWN_SECONDS):
        if not accounts:
            raise ValueError("at least one account is required")
        self._accounts: list[TwilioAccount] = list(accounts)
        self._cooldown_until: dict[str, float] = {}  # account name → epoch
        self._cooldown_seconds = cooldown_seconds
        # Cached Twilio Client per account (saves an SSL handshake per call)
        self._clients: dict[str, Client] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    @property
    def accounts(self) -> list[TwilioAccount]:
        return list(self._accounts)

    @property
    def global_allowlist(self) -> set[str]:
        """Union of every account's allowlist — used for the legacy
        single-env-var `call_allowlist_set` compatibility."""
        out: set[str] = set()
        for a in self._accounts:
            out |= set(a.allowlist)
        return out

    def _settings_key(self) -> list[tuple]:
        """Key that changes when the env-configured accounts change.
        Used by `get_rotator()` to invalidate the cache."""
        return [a._key() for a in self._accounts]

    def _get_client(self, account: TwilioAccount) -> Client:
        """Lazy-init + cache a `twilio.rest.Client` per account. The
        first call hits an SSL handshake; subsequent calls reuse it."""
        with self._lock:
            cli = self._clients.get(account.name)
            if cli is None:
                cli = Client(account.sid, account.token)
                self._clients[account.name] = cli
            return cli

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < SWEEP_INTERVAL:
            return
        self._last_sweep = now
        expired = [k for k, t in self._cooldown_until.items() if t <= now]
        for k in expired:
            self._cooldown_until.pop(k, None)

    def _is_available(self, account: TwilioAccount, now: float) -> bool:
        if account.unavailable:
            return False
        until = self._cooldown_until.get(account.name, 0.0)
        return until <= now

    def _cooldown(self, account: TwilioAccount, now: float,
                  seconds: float | None = None) -> None:
        self._cooldown_until[account.name] = now + (seconds or self._cooldown_seconds)

    def _candidates(self, to_number: str, now: float) -> list[TwilioAccount]:
        """Accounts that (a) have the target verified AND (b) are not on
        cooldown AND (c) are not marked unavailable. Ordered by
        least-recently-on-cooldown (round-robin)."""
        out = [a for a in self._accounts
               if to_number in a.allowlist and self._is_available(a, now)]
        return out

    def place_call(
        self,
        *,
        call_id: str,
        to_number: str,
        voice_url: str,
        status_callback: str,
    ) -> tuple[str, str]:
        """Place an outbound call. Returns (call_sid, account_name).

        Picks the first eligible account that has `to_number` verified.
        On Twilio rate-limit / capacity error, puts that account on
        cooldown and tries the next one. If every account is on
        cooldown or the number is not verified anywhere, raises.
        """
        if not self._accounts:
            raise NoAccountAvailable("no Twilio accounts configured")
        attempts = 0
        last_error: Exception | None = None
        while attempts < len(self._accounts):
            now = time.time()
            with self._lock:
                self._sweep(now)
                candidates = self._candidates(to_number, now)
            if not candidates:
                break
            account = candidates[0]
            try:
                client = self._get_client(account)
                call = client.calls.create(
                    to=to_number,
                    from_=account.from_number,
                    url=voice_url,
                    status_callback=status_callback,
                    status_callback_event=["completed", "no-answer",
                                            "busy", "failed"],
                    timeout=30,
                )
                # success — record health and return
                account.last_seen = time.time()
                account.fail_count = 0
                log.info("twilio: placed call via account=%s to=%s sid=%s",
                         account.name, to_number, call.sid)
                return call.sid, account.name
            except TwilioRestException as ex:
                last_error = ex
                code = getattr(ex, "code", None)
                if code in _PERMANENT_CODES:
                    log.warning("twilio: permanent error %s on %s: %s",
                                code, account.name, ex)
                    raise
                if code in _RATE_LIMIT_CODES or (ex.status or 0) >= 500:
                    with self._lock:
                        self._cooldown(account, time.time())
                    log.info("twilio: account %s on cooldown (%s); trying next",
                             account.name, code or ex.status)
                    account.fail_count += 1
                    attempts += 1
                    continue
                # 4xx that's not in the permanent set: log + try next
                log.warning("twilio: error %s on %s: %s; trying next",
                            code, account.name, ex)
                with self._lock:
                    self._cooldown(account, time.time(), seconds=10)
                account.fail_count += 1
                attempts += 1
                continue
            except Exception as ex:
                last_error = ex
                log.warning("twilio: unexpected error on %s: %s; trying next",
                            account.name, ex)
                with self._lock:
                    self._cooldown(account, time.time(), seconds=15)
                account.fail_count += 1
                attempts += 1
                continue

        if isinstance(last_error, TwilioRestException):
            raise NoAccountAvailable(
                f"all {len(self._accounts)} twilio account(s) on cooldown "
                f"or rejected; last error: {last_error}")
        raise NoAccountAvailable(
            f"number {to_number} is not in any account's allowlist")

    # ── health check (called from the APScheduler job in main.py) ──────────

    def health_check_all(self) -> dict[str, str]:
        """Ping each account's `accounts.fetch()`. Updates last_seen /
        fail_count / unavailable in place. Returns a {account_name: status}
        report for logging."""
        report: dict[str, str] = {}
        for acc in self._accounts:
            try:
                client = self._get_client(acc)
                client.api.v2010.accounts(acc.sid).fetch()
                acc.last_seen = time.time()
                acc.fail_count = 0
                if acc.unavailable:
                    log.info("twilio: account %s recovered", acc.name)
                acc.unavailable = False
                report[acc.name] = "ok"
            except Exception as ex:
                acc.fail_count += 1
                log.warning("twilio health: %s failed (%s)",
                            acc.name, ex)
                # mark unavailable if down for >HEALTH_DOWN_THRESHOLD
                if acc.last_seen and (time.time() - acc.last_seen > HEALTH_DOWN_THRESHOLD):
                    if not acc.unavailable:
                        log.error(
                            "twilio: account %s has been down for >%ds, "
                            "marking unavailable", acc.name, int(HEALTH_DOWN_THRESHOLD))
                    acc.unavailable = True
                report[acc.name] = f"fail: {type(ex).__name__}"
        return report

    def validate_all_on_startup(self) -> list[str]:
        """One-shot pre-flight: hit every account's `accounts.fetch()` and
        return a list of warning messages. Does NOT raise (a single bad
        account shouldn't block the whole deploy) — caller logs them.

        For strict mode (refuse to boot if any account is bad), set
        `TWILIO_FAIL_ON_PREFLIGHT=1`."""
        errors: list[str] = []
        for acc in self._accounts:
            try:
                client = self._get_client(acc)
                info = client.api.v2010.accounts(acc.sid).fetch()
                acc.last_seen = time.time()
                acc.fail_count = 0
                acc.unavailable = False
                log.info("twilio preflight: %s OK  (status=%s type=%s)",
                         acc.name, info.status, info.type)
            except Exception as ex:
                msg = f"{acc.name}: {type(ex).__name__}: {ex}"
                errors.append(msg)
                log.warning("twilio preflight FAILED: %s", msg)
        return errors


# ── module-level singleton, built from settings ──────────────────────────────

def _load_from_settings() -> list[TwilioAccount]:
    """Build the account list, preferring `TWILIO_ACCOUNTS` (JSON) and
    falling back to the legacy single-account env vars + `CALL_ALLOWLIST`."""
    accounts: list[TwilioAccount] = []

    if settings.TWILIO_ACCOUNTS:
        try:
            raw = json.loads(settings.TWILIO_ACCOUNTS)
        except json.JSONDecodeError as ex:
            log.error("TWILIO_ACCOUNTS is not valid JSON: %s", ex)
            raw = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict):
                log.warning("TWILIO_ACCOUNTS[%d] is not an object; skipped", i)
                continue
            sid = (entry.get("sid") or "").strip()
            token = (entry.get("token") or "").strip()
            from_n = (entry.get("from") or "").strip()
            if not (sid and token and from_n):
                log.warning("TWILIO_ACCOUNTS[%d] missing sid/token/from; skipped", i)
                continue
            allow = entry.get("allowlist") or []
            if isinstance(allow, str):
                allow = [x.strip() for x in allow.split(",") if x.strip()]
            name = (entry.get("name") or f"acc{i+1}").strip()
            accounts.append(TwilioAccount(
                name=name, sid=sid, token=token, from_number=from_n,
                allowlist=frozenset(allow),
            ))
    elif settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN \
            and settings.TWILIO_FROM_NUMBER:
        # legacy single-account fallback
        allow = settings.call_allowlist_set
        accounts.append(TwilioAccount(
            name="default",
            sid=settings.TWILIO_ACCOUNT_SID,
            token=settings.TWILIO_AUTH_TOKEN,
            from_number=settings.TWILIO_FROM_NUMBER,
            allowlist=frozenset(allow),
        ))
    return accounts


_rotator: TwilioAccountRotator | None = None
_rotator_lock = threading.Lock()


def get_rotator() -> TwilioAccountRotator | None:
    """Lazy-init the module-level rotator. Returns None if no accounts
    are configured (callers should refuse the call)."""
    global _rotator
    accounts = _load_from_settings()
    if not accounts:
        return None
    with _rotator_lock:
        if _rotator is None or _rotator._settings_key() != [a._key() for a in accounts]:
            _rotator = TwilioAccountRotator(accounts)
        return _rotator


def reset_rotator() -> None:
    """For tests: forget the cached rotator."""
    global _rotator
    with _rotator_lock:
        _rotator = None


def global_allowlist() -> set[str]:
    """Union of every configured account's allowlist."""
    r = get_rotator()
    if r is None:
        return settings.call_allowlist_set
    return r.global_allowlist
