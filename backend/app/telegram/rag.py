"""RAG engine for Telegram bot — keyword matching + Groq LLM.

No vector DB. Loads protocol JSONs + AMR knowledge at startup.
On each query: keyword-match to find relevant chunks, send to Groq with context.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger("telegram.rag")

# ── knowledge base ────────────────────────────────────────────────────────────

_KB_DIR = Path(__file__).resolve().parent.parent / "protocols"
_AMR_DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "03_PROTOCOLS_AMR.md"

_knowledge: list[dict] = []


def _load_knowledge() -> None:
    """Load protocol JSONs + AMR doc into flat knowledge chunks."""
    global _knowledge
    if _knowledge:
        return

    # protocol JSONs
    for p in _KB_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            _knowledge.append({
                "id": data.get("id", p.stem),
                "type": "protocol",
                "title_en": data.get("name_en", ""),
                "title_kn": data.get("name_kn", ""),
                "content": _summarize_protocol(data),
                "keywords": _extract_keywords(data),
                "sheet_bullets": data.get("sheet", {}).get("bullets_kn", []),
                "nodes": data.get("nodes", {}),
            })
        except Exception as e:
            log.warning("failed to load protocol %s: %s", p, e)

    # AMR doc
    if _AMR_DOC.exists():
        text = _AMR_DOC.read_text()
        _knowledge.append({
            "id": "amr_guidelines",
            "type": "amr",
            "title_en": "AMR Guidelines",
            "title_kn": "AMR ಮಾರ್ಗದರ್ಶನ",
            "content": text[:4000],  # first 4000 chars
            "keywords": ["amr", "antibiotic", "resistance", "aware", "watch",
                         "access", "reserve", "self-medication", "leftover",
                         "course", "course_days", "antibiotic_course"],
        })

    log.info("loaded %d knowledge chunks", len(_knowledge))


def _summarize_protocol(data: dict) -> str:
    """Extract human-readable summary from protocol JSON."""
    lines = []
    lines.append(f"Protocol: {data.get('name_en', '')} ({data.get('name_kn', '')})")
    lines.append(f"Schedule: Days {data.get('schedule_days', [])}")
    lines.append(f"Condition: {data.get('condition', '')}")

    for nid, node in data.get("nodes", {}).items():
        if node.get("type") == "question":
            opts = []
            for digit, opt in node.get("options", {}).items():
                score = opt.get("score", 0)
                reason = opt.get("reason", "")
                tag = "OK" if score == 0 else ("WARNING" if score < 5 else "RED FLAG")
                opts.append(f"  {digit}: [{tag}] {reason}")
            lines.append(f"Q ({nid}): options: {', '.join(opts)}")

    bullets = data.get("sheet", {}).get("bullets_kn", [])
    if bullets:
        lines.append("Key instructions (Kannada):")
        for b in bullets:
            lines.append(f"  - {b}")

    return "\n".join(lines)


def _extract_keywords(data: dict) -> list[str]:
    """Extract searchable keywords from protocol."""
    words = set()
    words.add(data.get("id", ""))
    words.add(data.get("name_en", "").lower())
    words.add(data.get("condition", ""))
    for nid, node in data.get("nodes", {}).items():
        words.add(nid)
        for opt in node.get("options", {}).values():
            r = opt.get("reason", "").lower()
            words.update(r.split())
    return [w for w in words if len(w) > 2]


# ── retrieval ─────────────────────────────────────────────────────────────────

def retrieve(query: str, patient_context: dict | None = None) -> str:
    """Retrieve relevant knowledge chunks and format as context for Groq."""
    _load_knowledge()
    q_lower = query.lower()
    scored: list[tuple[float, dict]] = []

    for chunk in _knowledge:
        score = 0.0
        # keyword match
        for kw in chunk.get("keywords", []):
            if kw.lower() in q_lower:
                score += 2.0
        # title match
        if chunk["title_en"].lower() in q_lower or chunk["title_kn"] in query:
            score += 3.0
        # direct content match (simple substring)
        if q_lower[:8] in chunk.get("content", "").lower():
            score += 1.0

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: -x[0])
    top = scored[:3] if scored else [(1.0, _knowledge[0])] if _knowledge else []

    context_parts = []
    for _, chunk in top:
        context_parts.append(f"=== {chunk['title_en']} ({chunk['title_kn']}) ===\n{chunk['content']}")

    # add patient-specific context
    if patient_context:
        ctx = (
            f"\n=== PATIENT INFO ===\n"
            f"Name: {patient_context.get('name', 'unknown')}\n"
            f"Age: {patient_context.get('age', 'unknown')}\n"
            f"Condition: {patient_context.get('condition', 'unknown')}\n"
            f"Protocol: {patient_context.get('protocol', 'unknown')}\n"
            f"Medications: {patient_context.get('meds', 'none')}\n"
            f"Discharge date: {patient_context.get('discharge_date', 'unknown')}\n"
        )
        context_parts.append(ctx)

    return "\n\n".join(context_parts) if context_parts else "No specific knowledge found for this query."


# ── Groq call ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_PATIENT = (
    "You are a compassionate health assistant for District Hospital, Karnataka. "
    "You help patients and caregivers understand their medications, recovery, "
    "and when to seek help. Respond in the SAME LANGUAGE the user writes in "
    "(Kannada if they write in Kannada, English if English). "
    "Be concise (2-4 sentences max). Always remind: take meds on time, "
    "complete the full course, call 104/108 for emergencies. "
    "Never diagnose. Never prescribe. Never say 'consult a doctor' if the "
    "question is about basic medication guidance — just answer directly."
)

SYSTEM_PROMPT_STAFF = (
    "You are a medical reference assistant for healthcare workers at District Hospital, Karnataka. "
    "Answer questions about protocols, AMR guidelines, medication dosing, and clinical workflows. "
    "Respond in the SAME LANGUAGE the user writes in. "
    "Be precise and reference specific protocol nodes when relevant. "
    "If unsure, say so — never guess on clinical facts."
)


def ask_llm(
    query: str,
    context: str,
    patient_context: dict | None = None,
    is_staff: bool = False,
) -> str:
    """Call Groq LLM with retrieved context + user query."""
    if not settings.GROQ_API_KEY:
        return _fallback_response(query, patient_context, is_staff)

    # sanitize: strip control chars, limit length, remove potential injection markers
    import re
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', query)[:500]
    # neutralize common prompt injection patterns
    sanitized = sanitized.replace("ignore previous", "ignore prior").replace("ignore all previous", "ignore all prior")

    system_prompt = SYSTEM_PROMPT_STAFF if is_staff else SYSTEM_PROMPT_PATIENT
    user_msg = f"Context:\n{context[:2000]}\n\nUser question: {sanitized}"

    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 300,
                "temperature": 0.3,
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        log.warning("groq %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("groq failed: %s", e)

    return _fallback_response(query, patient_context, is_staff)


def _fallback_response(query: str, patient_ctx: dict | None, is_staff: bool) -> str:
    """Deterministic fallback when LLM is unavailable."""
    q = query.lower()

    if any(w in q for w in ["med", "medicine", "tablet", "ಔಷಧ", "ಗೋಳಿ"]):
        if patient_ctx and patient_ctx.get("meds"):
            return (
                f"Your medications: {patient_ctx['meds']}\n"
                "Take them on time, complete the full course. "
                "For emergencies call 104."
            )
        return "Please verify your phone number first to see your medications. Send /verify to start."

    if any(w in q for w in ["wound", "ಗಾಯ", "surgery", "operation"]):
        return ("Keep the wound clean and dry. Wash hands before touching near it. "
                "If you see pus, bleeding, or have fever — go to hospital immediately. "
                "Call 104/108 for emergencies.")

    if any(w in q for w in ["antibiotic", "ಯಾಂಟಿಬಯಾಟಿಕ್", "course"]):
        return ("Always complete the full antibiotic course. Never share or save leftover tablets. "
                "Antibiotics don't work for viral fever — only take them if prescribed.")

    if any(w in q for w in ["fever", "ಜ್ವರ", "temperature"]):
        return ("Rest and drink plenty of fluids. You can take paracetamol for fever. "
                "If fever lasts more than 3 days or you have breathing difficulty, go to hospital.")

    if is_staff:
        return ("I can help with protocol questions. Try asking about wound_care, "
                "antibiotic_course, or fever_viral protocols. "
                "For AMR guidelines, ask about 'AMR' or 'antibiotic resistance'.")

    return ("I can help with your recovery. Try asking about your medications, "
            "wound care, or fever management. Send /meds to see your prescriptions. "
            "For emergencies call 104 or 108.")
