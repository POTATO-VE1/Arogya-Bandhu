"""Analytics router — L3 ward summary + L4 district dashboard.

All endpoints here are under `/api/analytics/...`. Per-protocol
analytics lives in `routers/protocols.py` (path `/api/protocols/{pid}/analytics`)
because that URL is protocol-scoped.
"""
import json
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import Enrollment, Escalation, FollowupCall, User
from app.tzutil import days_ist_window

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _ward_in_scope(user: User, requested_ward: str | None) -> str | None:
    """Enforce server-side ward scoping for L3 ward-summary.

    Admin/doctor → whatever the param says. Nurse/staff → their own
    ward (the param is ignored — prevents IDOR).
    """
    if user.role in ("admin", "doctor"):
        return requested_ward
    return user.ward


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ─────────────────────────────────────────────────────────────────────────────
# Distinct wards for the admin/doctor ward selector (used by L1 intake)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/wards")
def list_wards(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Return sorted, distinct ward names in this hospital. Used by the
    admin/doctor ward selector in the intake form (L1)."""
    rows = (db.query(Enrollment.ward)
            .filter(Enrollment.hospital_code == user.hospital_code,
                    Enrollment.ward.isnot(None))
            .distinct().all())
    return sorted({r[0] for r in rows if r[0]})


# ─────────────────────────────────────────────────────────────────────────────
# L3: ward summary
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/ward-summary")
def ward_summary(ward: str | None = Query(None),
                 days: int = Query(7, ge=1, le=90),
                 user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Aggregated KPIs for one ward (or the user's own ward for nurses)
    over the last N days (IST-anchored)."""
    ward = _ward_in_scope(user, ward)
    start, end = days_ist_window(days)
    # start[:10] is the IST date (YYYY-MM-DD)
    start_date, end_date = start[:10], end[:10]
    e_q = (db.query(Enrollment)
           .filter(Enrollment.hospital_code == user.hospital_code,
                   Enrollment.discharge_date >= start_date,
                   Enrollment.discharge_date <= end_date))
    if ward is not None:
        e_q = e_q.filter(Enrollment.ward == ward)
    enrollments = e_q.all()
    eids = [e.id for e in enrollments]
    if not eids:
        return _empty_ward_summary(ward, days)

    status_counts = (db.query(Enrollment.status, func.count())
                     .filter(Enrollment.id.in_(eids))
                     .group_by(Enrollment.status).all())
    sc = dict(status_counts)
    completed = sc.get("completed", 0)
    active = sc.get("active", 0)
    cancelled = sc.get("cancelled", 0)

    call_status_counts = (db.query(FollowupCall.status, func.count())
                          .filter(FollowupCall.enrollment_id.in_(eids))
                          .group_by(FollowupCall.status).all())
    cs = dict(call_status_counts)
    calls_completed = cs.get("completed", 0)
    calls_no_answer = cs.get("no_answer", 0) + cs.get("failed", 0)
    reach_rate = round(calls_completed / max(calls_completed + calls_no_answer, 1), 3)

    open_esc = (db.query(Escalation)
                .filter(Escalation.enrollment_id.in_(eids),
                        Escalation.status.in_(["open", "acked"]))
                .count())
    red_esc = (db.query(Escalation)
               .filter(Escalation.enrollment_id.in_(eids),
                       Escalation.level == "red")
               .count())
    red_flag_rate = round(red_esc / max(len(enrollments), 1), 3)

    # Avg ack hours
    ack_times = (db.query(Escalation.created_at, Escalation.acked_at)
                 .filter(Escalation.enrollment_id.in_(eids),
                         Escalation.acked_at.isnot(None)).all())
    avg_ack = None
    if ack_times:
        secs = sum((_parse_dt(a) - _parse_dt(c)).total_seconds()
                   for c, a in ack_times)
        avg_ack = round(secs / len(ack_times) / 3600, 2)

    outcomes = Counter(e.outcome for e in enrollments if e.outcome)

    # Call completion by day_index
    by_day_rows = (db.query(FollowupCall.day_index, FollowupCall.status, func.count())
                   .filter(FollowupCall.enrollment_id.in_(eids))
                   .group_by(FollowupCall.day_index, FollowupCall.status).all())
    day_map: dict[int, dict[str, int]] = {}
    for d, st, c in by_day_rows:
        day_map.setdefault(d, {"completed": 0, "total": 0})
        day_map[d]["total"] += c
        if st == "completed":
            day_map[d]["completed"] += c
    return {
        "ward": ward,
        "period_days": days,
        "total_enrolled": len(enrollments),
        "active": active,
        "completed": completed,
        "cancelled": cancelled,
        "reach_rate": reach_rate,
        "red_flag_rate": red_flag_rate,
        "open_escalations": open_esc,
        "avg_ack_hours": avg_ack,
        "outcome_breakdown": dict(outcomes),
        "call_completion_by_day": [
            {"day_index": d, "completed": v["completed"], "total": v["total"]}
            for d, v in sorted(day_map.items())
        ],
    }


def _empty_ward_summary(ward: str | None, days: int) -> dict:
    return {
        "ward": ward,
        "period_days": days,
        "total_enrolled": 0,
        "active": 0,
        "completed": 0,
        "cancelled": 0,
        "reach_rate": None,
        "red_flag_rate": None,
        "open_escalations": 0,
        "avg_ack_hours": None,
        "outcome_breakdown": {},
        "call_completion_by_day": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# L4: district dashboard
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/district-dashboard")
def district_dashboard(days: int = Query(30, ge=1, le=90),
                        user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """District-level rollup. Admin/doctor only. Per-ward and per-protocol
    breakdowns + top escalation reasons."""
    if user.role not in ("admin", "doctor"):
        raise HTTPException(403, "admin or doctor only")
    start, end = days_ist_window(days)
    start_date, end_date = start[:10], end[:10]
    enrollments = (db.query(Enrollment)
                   .filter(Enrollment.hospital_code == user.hospital_code,
                           Enrollment.discharge_date >= start_date,
                           Enrollment.discharge_date <= end_date)
                   .all())
    eids = [e.id for e in enrollments]
    if not eids:
        return _empty_district_dashboard(days)

    # Per-ward breakdown
    ward_stats: dict[str, dict] = {}
    for e in enrollments:
        key = e.ward or "Unknown"
        ws = ward_stats.setdefault(key, {
            "enrolled": 0, "active": 0, "completed": 0, "cancelled": 0,
            "calls": 0, "calls_done": 0, "red": 0,
        })
        ws["enrolled"] += 1
        if e.status == "active":
            ws["active"] += 1
        elif e.status == "completed":
            ws["completed"] += 1
        elif e.status == "cancelled":
            ws["cancelled"] += 1

    # Calls by ward
    call_rows = (db.query(Enrollment.ward, FollowupCall.status, func.count())
                 .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                 .filter(Enrollment.id.in_(eids))
                 .group_by(Enrollment.ward, FollowupCall.status).all())
    for w, st, c in call_rows:
        ws = ward_stats.setdefault(w or "Unknown", {
            "enrolled": 0, "active": 0, "completed": 0, "cancelled": 0,
            "calls": 0, "calls_done": 0, "red": 0,
        })
        ws["calls"] += c
        if st == "completed":
            ws["calls_done"] += c

    # Red per ward
    red_rows = (db.query(Enrollment.ward, func.count())
                .join(Escalation, Escalation.enrollment_id == Enrollment.id)
                .filter(Escalation.enrollment_id.in_(eids),
                        Escalation.level == "red")
                .group_by(Enrollment.ward).all())
    for w, c in red_rows:
        ws = ward_stats.setdefault(w or "Unknown", {
            "enrolled": 0, "active": 0, "completed": 0, "cancelled": 0,
            "calls": 0, "calls_done": 0, "red": 0,
        })
        ws["red"] = c

    ward_breakdown = []
    for ward in sorted(ward_stats.keys()):
        ws = ward_stats[ward]
        ward_breakdown.append({
            "ward": ward,
            "enrolled": ws["enrolled"],
            "active": ws["active"],
            "completed": ws["completed"],
            "cancelled": ws["cancelled"],
            "red": ws["red"],
            "reach_rate": round(ws["calls_done"] / max(ws["calls"], 1), 3),
            "red_rate": round(ws["red"] / max(ws["enrolled"], 1), 3),
        })

    # Per-protocol breakdown
    proto_stats: dict[str, dict] = {}
    for e in enrollments:
        key = e.protocol_id
        ps = proto_stats.setdefault(key, {
            "enrolled": 0, "completed": 0, "calls_done": 0, "red": 0,
        })
        ps["enrolled"] += 1
        if e.status == "completed":
            ps["completed"] += 1

    proto_call_rows = (db.query(Enrollment.protocol_id, func.count())
                       .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                       .filter(Enrollment.id.in_(eids),
                               FollowupCall.status == "completed")
                       .group_by(Enrollment.protocol_id).all())
    for p, c in proto_call_rows:
        ps = proto_stats.setdefault(p, {
            "enrolled": 0, "completed": 0, "calls_done": 0, "red": 0,
        })
        ps["calls_done"] = c
    proto_red_rows = (db.query(Enrollment.protocol_id, func.count())
                       .join(Escalation, Escalation.enrollment_id == Enrollment.id)
                       .filter(Escalation.enrollment_id.in_(eids),
                               Escalation.level == "red")
                       .group_by(Enrollment.protocol_id).all())
    for p, c in proto_red_rows:
        ps = proto_stats.setdefault(p, {
            "enrolled": 0, "completed": 0, "calls_done": 0, "red": 0,
        })
        ps["red"] = c
    protocol_breakdown = []
    for p in sorted(proto_stats.keys()):
        ps = proto_stats[p]
        protocol_breakdown.append({
            "protocol": p,
            "enrolled": ps["enrolled"],
            "completion_rate": round(ps["completed"] / max(ps["enrolled"], 1), 3),
            "red_rate": round(ps["red"] / max(ps["enrolled"], 1), 3),
        })

    # Top escalation reasons (dedup case-insensitive, normalize)
    all_reasons: list[str] = []
    for (reasons_json,) in (db.query(Escalation.reasons)
                             .filter(Escalation.enrollment_id.in_(eids)).all()):
        try:
            rs = json.loads(reasons_json or "[]")
            all_reasons.extend(rs)
        except Exception:
            pass
    reason_counts = Counter(r.strip().lower() for r in all_reasons if r)
    top_reasons = [{"reason": r, "count": c}
                   for r, c in reason_counts.most_common(5)]

    # Total active
    total_active = sum(1 for e in enrollments if e.status == "active")
    # Total red (open or acked, i.e., not resolved)
    open_esc_ids = (db.query(Escalation.enrollment_id)
                    .filter(Escalation.enrollment_id.in_(eids),
                            Escalation.status.in_(["open", "acked"]))
                    .distinct().all())
    total_red = len(open_esc_ids)

    return {
        "total_enrolled": len(enrollments),
        "total_active": total_active,
        "total_red": total_red,
        "period_days": days,
        "ward_breakdown": ward_breakdown,
        "protocol_breakdown": protocol_breakdown,
        "top_escalation_reasons": top_reasons,
    }


def _empty_district_dashboard(days: int) -> dict:
    return {
        "total_enrolled": 0,
        "total_active": 0,
        "total_red": 0,
        "period_days": days,
        "ward_breakdown": [],
        "protocol_breakdown": [],
        "top_escalation_reasons": [],
    }
