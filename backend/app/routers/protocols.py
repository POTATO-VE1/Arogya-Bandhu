"""Protocols router — list, detail, and L5 analytics.

L5 (docs/10) analytics lives here because the URL is
`/api/protocols/{pid}/analytics` (a protocol-scoped route). The
analytics.py router handles `/api/analytics/...` (ward / district).
"""
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import Enrollment, Escalation, FollowupCall, User
from app.protocol_loader import get_protocol

router = APIRouter(prefix="/api/protocols", tags=["protocols"])


@router.get("")
def list_protocols(_=Depends(current_user)):
    from app.protocol_loader import protocol_meta_list
    return protocol_meta_list()


@router.get("/{pid}/detail")
def protocol_detail(pid: str, _=Depends(current_user)):
    """Return full protocol nodes with option reasons for label resolution."""
    try:
        proto = get_protocol(pid)
    except Exception:
        raise HTTPException(404, "protocol not found")
    questions = {}
    for node_id, node in proto.get("nodes", {}).items():
        if node.get("type") == "question":
            questions[node_id] = {
                "clip": node.get("clip", ""),
                "options": {
                    digit: {
                        "reason": opt.get("reason", ""),
                        "score": opt.get("score", 0),
                    }
                    for digit, opt in node.get("options", {}).items()
                },
            }
    return {
        "id": proto["id"],
        "name_en": proto.get("name_en", ""),
        "name_kn": proto.get("name_kn", ""),
        "questions": questions,
    }


# ── L5: per-protocol analytics ────────────────────────────────────────────────
@router.get("/{pid}/analytics")
def protocol_analytics(pid: str,
                        user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Aggregated metrics for a single protocol. Same hospital scope as
    the rest of the API. Includes the J5 pill_count_violations field,
    which is non-null only for antibiotic protocols."""
    try:
        proto = get_protocol(pid)
    except Exception:
        raise HTTPException(404, "protocol not found")

    enrollments = (db.query(Enrollment)
                   .filter(Enrollment.hospital_code == user.hospital_code,
                           Enrollment.protocol_id == pid)
                   .all())
    eids = [e.id for e in enrollments]

    if not eids:
        return _empty_protocol_analytics(pid, proto)

    completed = sum(1 for e in enrollments if e.status == "completed")
    red_count = (db.query(Escalation)
                 .filter(Escalation.enrollment_id.in_(eids),
                         Escalation.level == "red")
                 .count())

    # Last-call risk distribution (per enrollment, take the most recent
    # completed call's risk_level).
    risk_dist = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    for e in enrollments:
        last = (db.query(FollowupCall)
                .filter(FollowupCall.enrollment_id == e.id,
                        FollowupCall.status == "completed")
                .order_by(FollowupCall.completed_at.desc()).first())
        if last and last.risk_level in ("green", "yellow", "red"):
            risk_dist[last.risk_level] += 1
        else:
            risk_dist["unknown"] += 1

    outcomes = Counter(e.outcome for e in enrollments if e.outcome)

    # Avg ack hours
    ack_times = (db.query(Escalation.created_at, Escalation.acked_at)
                 .filter(Escalation.enrollment_id.in_(eids),
                         Escalation.acked_at.isnot(None)).all())
    avg_ack = None
    if ack_times:
        from datetime import datetime
        def _p(s):
            return datetime.fromisoformat(s)
        secs = sum((_p(a) - _p(c)).total_seconds() for c, a in ack_times)
        avg_ack = round(secs / len(ack_times) / 3600, 2)

    # pill_count_violations (J5): only meaningful for antibiotic protocols.
    # Count red escalations whose reasons include the pill-count text.
    pill_violations = None
    if pid == "antibiotic_course":
        pill_violations = (db.query(Escalation)
                          .filter(Escalation.enrollment_id.in_(eids),
                                  Escalation.level == "red",
                                  Escalation.reasons.like("%pill count%"))
                          .count())

    return {
        "protocol_id": pid,
        "protocol_name": proto.get("name_en", ""),
        "total_enrolled": len(enrollments),
        "completion_rate": round(completed / len(enrollments), 3),
        "red_flag_rate": round(red_count / len(enrollments), 3),
        "risk_distribution": risk_dist,
        "outcomes": dict(outcomes),
        "avg_ack_hours": avg_ack,
        "pill_count_violations": pill_violations,
    }


def _empty_protocol_analytics(pid: str, proto: dict) -> dict:
    return {
        "protocol_id": pid,
        "protocol_name": proto.get("name_en", ""),
        "total_enrolled": 0,
        "completion_rate": None,
        "red_flag_rate": None,
        "risk_distribution": {"green": 0, "yellow": 0, "red": 0, "unknown": 0},
        "outcomes": {},
        "avg_ack_hours": None,
        "pill_count_violations": None if pid == "antibiotic_course" else None,
    }
