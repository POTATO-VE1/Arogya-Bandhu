"""Groq LLM assist (docs/03 §10). Edge-only, flag-gated, fallback-safe.

The LLM assembles / selects — it NEVER invents clinical content.
OpenAI-compatible REST via httpx (no SDK). On ANY failure → deterministic templates.
Never used in the IVR/call path.

For free-tier rate-limit handling, multiple Groq API keys can be configured
(`GROQ_API_KEY` + `GROQ_API_KEYS=gsk_a,gsk_b,gsk_c`); requests are
round-robined across them, and any key that 429s is skipped for 60s.
See `app/llm_rotator.py`.
"""
from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.llm_rotator import AllKeysExhausted, get_rotator

log = logging.getLogger("llm")
DOSAGE_RE = re.compile(r"(?:\d+\s*)?(?:mg|ml)\b|\btablet\s+\d|\bdose\b", re.I)


def enabled() -> bool:
    return get_rotator() is not None


SYSTEM_SUGGEST = (
    "You are a discharge-triage assistant for a Karnataka government hospital. "
    "Given a condition label, choose exactly one follow-up protocol id from the "
    "provided list, and draft 3-5 short English home-care instruction sentences. "
    "Rules: no drug names, no dosages, no diagnosis claims, no emergency advice "
    "beyond 'go to hospital if worsening'. Reply ONLY as JSON: "
    '{"protocol_id": "...", "instructions_en": ["..."], "note": "one short line"}.'
)


def _available_protocol_ids() -> list[str]:
    from app.protocol_loader import get_protocols
    return list(get_protocols().keys())


def suggest_protocol(condition_label: str) -> dict | None:
    """Returns {protocol_id, instructions_en, note} or None on failure/disabled."""
    rotator = get_rotator()
    if rotator is None:
        return None
    payload = {
        "model": settings.LLM_MODEL, "temperature": 0.2, "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_SUGGEST},
            {"role": "user", "content": (
                f"Condition: {condition_label}. "
                f"Available protocol ids: {_available_protocol_ids()}")},
        ],
    }
    try:
        r = rotator.call(payload, timeout=4.0)
    except AllKeysExhausted as ex:
        log.warning("groq suggest: %s", ex)
        return None
    except Exception as ex:
        log.warning("groq suggest failed: %s", ex)
        return None
    if r.status_code != 200:
        log.warning("groq suggest http %s", r.status_code)
        return None
    try:
        data = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as ex:
        log.warning("groq suggest parse: %s", ex)
        return None
    return _validate_suggest(data)


def _validate_suggest(data: dict) -> dict | None:
    try:
        pid = data["protocol_id"]
        if pid not in _available_protocol_ids():
            return None
        inst = [str(s) for s in data.get("instructions_en", []) if isinstance(s, str)]
        inst = [i[:120] for i in inst if not DOSAGE_RE.search(i)][:6]
        if not inst:
            return None
        note = str(data.get("note", ""))[:200]
        return {"protocol_id": pid, "instructions_en": inst, "note": note,
                "source": "llm"}
    except Exception:
        return None


# ── sheet personalization: select-by-index from approved Kannada bank ─────────
SYSTEM_SHEET = (
    "Given a Kannada caregiver instruction bullet bank (an array of strings) and a "
    "condition, return the indices (0-based) of the 4-5 bullets most relevant to "
    'this condition, in order. Reply ONLY as JSON: {"bullet_indices":[...]}'
)


def personalize_sheet(bullets_kn: list[str], condition_label: str) -> dict | None:
    """Returns {bullets_kn:[...], source:'llm'} or None on failure."""
    rotator = get_rotator()
    if rotator is None:
        return None
    payload = {
        "model": settings.LLM_MODEL, "temperature": 0.2, "max_tokens": 300,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_SHEET},
            {"role": "user", "content": f"Condition: {condition_label}.\nBank: {bullets_kn}"},
        ],
    }
    try:
        r = rotator.call(payload, timeout=4.0)
    except AllKeysExhausted as ex:
        log.warning("groq sheet: %s", ex)
        return None
    except Exception as ex:
        log.warning("groq sheet failed: %s", ex)
        return None
    if r.status_code != 200:
        return None
    try:
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        idxs = [int(i) for i in data["bullet_indices"]]
        if not idxs or any(not 0 <= i < len(bullets_kn) for i in idxs):
            return None
        return {"bullets_kn": [bullets_kn[i] for i in idxs[:6]], "source": "llm"}
    except Exception as ex:
        log.warning("groq sheet parse: %s", ex)
        return None


def default_sheet(bullets_kn: list[str]) -> dict:
    return {"bullets_kn": bullets_kn[:5], "source": "template"}
