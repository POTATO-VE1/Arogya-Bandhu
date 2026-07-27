"""Comprehensive admin/doctor/superadmin dashboard.

Returns every number an admin needs in a single call so the UI can
render without N round-trips. Same shape for all roles; the only
difference is the scope:

- nurse/staff → their ward only (uses `user.ward`)
- doctor / admin → their hospital
- superadmin → all hospitals (or `?hospital=CODE` to scope to one)

All counts are hospital_code-scoped via `hospital_scope()`.
The role guard already blocks non-admin roles from reaching this router.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db
from app.deps import current_user, hospital_scope, is_cross_hospital, require_admin
from app.models import (
    AuditLog, CallResponse, Enrollment, EnrollmentMed, Escalation,
    FollowupCall, Hospital, Patient, User,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@router.get("/overview")
def overview(
    hospital: str | None = Query(None, description="Superadmin only: scope to a hospital code"),
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Everything an admin needs, one call. The shape is fixed so the
    frontend can render the same dashboard for any role."""
    scope = hospital_scope(user, hospital)
    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)
    period_start = today_start - timedelta(days=days)
    period_start_iso = _iso(period_start)
    today_iso = _iso(today_start)

    # ── Base enrollment query (always hospital-scoped) ──────────────────
    def _enrollments():
        q = db.query(Enrollment)
        if scope is not None:
            q = q.filter(Enrollment.hospital_code == scope)
        else:
            # superadmin with no scope: all hospitals
            pass
        return q

    enrollments = _enrollments().all()
    eids = [e.id for e in enrollments]

    # ── 1. Patient counts ────────────────────────────────────────────────
    enrolled_total = len(enrollments)
    enrolled_active = sum(1 for e in enrollments if e.status == "active")
    enrolled_completed = sum(1 for e in enrollments if e.status == "completed")
    enrolled_cancelled = sum(1 for e in enrollments if e.status == "cancelled")
    pids = list({e.patient_id for e in enrollments})
    patients_total = len(pids)
    new_patients_today = 0
    if pids:
        new_patients_today = (db.query(Patient)
            .filter(Patient.id.in_(pids),
                    Patient.created_at >= today_iso).count())

    # ── 2. Call counts ───────────────────────────────────────────────────
    if eids:
        call_q = db.query(FollowupCall).filter(
            FollowupCall.enrollment_id.in_(eids))
        calls_total = call_q.count()
        calls_today = call_q.filter(
            FollowupCall.scheduled_at >= today_iso).count()
        calls_this_week = call_q.filter(
            FollowupCall.scheduled_at >= _iso(week_start)).count()
        calls_this_month = call_q.filter(
            FollowupCall.scheduled_at >= _iso(month_start)).count()
        calls_completed = call_q.filter(
            FollowupCall.status == "completed").count()
        calls_no_answer = call_q.filter(
            FollowupCall.status.in_(["no_answer", "failed"])).count()
        calls_in_progress = call_q.filter(
            FollowupCall.status == "in_progress").count()
        calls_ringing = call_q.filter(
            FollowupCall.status == "ringing").count()
        calls_pending = call_q.filter(
            FollowupCall.status == "pending").count()
        # Risk distribution (latest completed call per enrollment)
        risk_green = risk_yellow = risk_red = 0
        for e in enrollments:
            last = (db.query(FollowupCall)
                    .filter(FollowupCall.enrollment_id == e.id,
                            FollowupCall.status == "completed")
                    .order_by(FollowupCall.completed_at.desc()).first())
            if last and last.risk_level == "green":
                risk_green += 1
            elif last and last.risk_level == "yellow":
                risk_yellow += 1
            elif last and last.risk_level == "red":
                risk_red += 1
    else:
        calls_total = calls_today = calls_this_week = calls_this_month = 0
        calls_completed = calls_no_answer = calls_in_progress = 0
        calls_ringing = calls_pending = 0
        risk_green = risk_yellow = risk_red = 0
    reach_rate = (round(calls_completed / max(calls_completed + calls_no_answer, 1), 3)
                  if (calls_completed + calls_no_answer) else None)

    # ── 3. Escalations ───────────────────────────────────────────────────
    esc_q = db.query(Escalation)
    if scope is not None:
        esc_q = esc_q.filter(Escalation.hospital_code == scope)
    esc_total = esc_q.count()
    esc_open = esc_q.filter(Escalation.status == "open").count()
    esc_acked = esc_q.filter(Escalation.status == "acked").count()
    esc_resolved = esc_q.filter(Escalation.status == "resolved").count()
    esc_resolved_today = esc_q.filter(
        Escalation.status == "resolved",
        Escalation.resolved_at >= today_iso).count()
    esc_open_today = esc_q.filter(
        Escalation.created_at >= today_iso,
        Escalation.status.in_(["open", "acked"])).count()
    # avg ack time in hours
    ack_pairs = esc_q.filter(
        Escalation.acked_at.isnot(None)).all()
    if ack_pairs:
        deltas = []
        for x in ack_pairs:
            c = _parse_dt(x.created_at)
            a = _parse_dt(x.acked_at)
            if c and a:
                deltas.append((a - c).total_seconds() / 3600)
        avg_ack_hours = round(sum(deltas) / len(deltas), 2) if deltas else None
    else:
        avg_ack_hours = None
    # avg resolve time
    res_pairs = esc_q.filter(
        Escalation.resolved_at.isnot(None)).all()
    if res_pairs:
        deltas = []
        for x in res_pairs:
            c = _parse_dt(x.created_at)
            r = _parse_dt(x.resolved_at)
            if c and r:
                deltas.append((r - c).total_seconds() / 3600)
        avg_resolve_hours = round(sum(deltas) / len(deltas), 2) if deltas else None
    else:
        avg_resolve_hours = None

    # ── 4. Outcomes ──────────────────────────────────────────────────────
    outcome_counter: Counter = Counter()
    for e in enrollments:
        if e.outcome:
            outcome_counter[e.outcome] += 1
    outcomes = dict(outcome_counter)

    # ── 5. Per-ward breakdown ────────────────────────────────────────────
    ward_stats: dict[str, dict] = defaultdict(lambda: {
        "enrolled": 0, "active": 0, "completed": 0, "cancelled": 0,
        "calls": 0, "calls_done": 0, "red": 0, "yellow": 0, "green": 0,
    })
    for e in enrollments:
        ws = ward_stats[e.ward or "(unassigned)"]
        ws["enrolled"] += 1
        if e.status == "active":
            ws["active"] += 1
        elif e.status == "completed":
            ws["completed"] += 1
        elif e.status == "cancelled":
            ws["cancelled"] += 1
    if eids:
        # per-ward call counts
        ward_calls = (db.query(Enrollment.ward, func.count(FollowupCall.id))
                      .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                      .filter(FollowupCall.enrollment_id.in_(eids))
                      .group_by(Enrollment.ward).all())
        for w, c in ward_calls:
            ward_stats[w or "(unassigned)"]["calls"] += c
        ward_done = (db.query(Enrollment.ward, func.count(FollowupCall.id))
                     .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                     .filter(FollowupCall.enrollment_id.in_(eids),
                             FollowupCall.status == "completed")
                     .group_by(Enrollment.ward).all())
        for w, c in ward_done:
            ward_stats[w or "(unassigned)"]["calls_done"] += c
        # per-ward red risk
        ward_red = (db.query(Enrollment.ward, func.count(FollowupCall.id))
                    .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                    .filter(FollowupCall.enrollment_id.in_(eids),
                            FollowupCall.risk_level == "red",
                            FollowupCall.status == "completed")
                    .group_by(Enrollment.ward).all())
        for w, c in ward_red:
            ward_stats[w or "(unassigned)"]["red"] += c
    ward_breakdown = []
    for ward, ws in sorted(ward_stats.items()):
        reach = (round(ws["calls_done"] / max(ws["calls"], 1), 3)
                 if ws["calls"] else None)
        ward_breakdown.append({
            "ward": ward,
            "enrolled": ws["enrolled"],
            "active": ws["active"],
            "completed": ws["completed"],
            "cancelled": ws["cancelled"],
            "calls": ws["calls"],
            "calls_done": ws["calls_done"],
            "red_flags": ws["red"],
            "reach_rate": reach,
        })

    # ── 6. Per-protocol breakdown ────────────────────────────────────────
    proto_stats: dict[str, dict] = defaultdict(lambda: {
        "enrolled": 0, "completed": 0, "red": 0, "yellow": 0, "green": 0,
        "calls": 0, "calls_done": 0,
    })
    for e in enrollments:
        ps = proto_stats[e.protocol_id]
        ps["enrolled"] += 1
        if e.status == "completed":
            ps["completed"] += 1
    if eids:
        proto_red = (db.query(Enrollment.protocol_id, func.count(Escalation.id))
                     .join(Escalation, Escalation.enrollment_id == Enrollment.id)
                     .filter(Escalation.enrollment_id.in_(eids),
                             Escalation.level == "red")
                     .group_by(Enrollment.protocol_id).all())
        for p, c in proto_red:
            proto_stats[p]["red"] += c
        proto_calls = (db.query(Enrollment.protocol_id, func.count(FollowupCall.id))
                       .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                       .filter(FollowupCall.enrollment_id.in_(eids))
                       .group_by(Enrollment.protocol_id).all())
        for p, c in proto_calls:
            proto_stats[p]["calls"] += c
        proto_done = (db.query(Enrollment.protocol_id, func.count(FollowupCall.id))
                      .join(FollowupCall, FollowupCall.enrollment_id == Enrollment.id)
                      .filter(FollowupCall.enrollment_id.in_(eids),
                              FollowupCall.status == "completed")
                      .group_by(Enrollment.protocol_id).all())
        for p, c in proto_done:
            proto_stats[p]["calls_done"] += c
    protocol_breakdown = []
    for proto, ps in sorted(proto_stats.items()):
        completion = (round(ps["completed"] / max(ps["enrolled"], 1), 3)
                      if ps["enrolled"] else None)
        red_rate = (round(ps["red"] / max(ps["enrolled"], 1), 3)
                    if ps["enrolled"] else None)
        reach = (round(ps["calls_done"] / max(ps["calls"], 1), 3)
                 if ps["calls"] else None)
        protocol_breakdown.append({
            "protocol": proto,
            "enrolled": ps["enrolled"],
            "completed": ps["completed"],
            "calls": ps["calls"],
            "calls_done": ps["calls_done"],
            "red_flags": ps["red"],
            "completion_rate": completion,
            "red_flag_rate": red_rate,
            "reach_rate": reach,
        })

    # ── 7. Per-staff performance ─────────────────────────────────────────
    # staff_metrics: calls_made, escalations_acked, escalations_resolved
    # scoped to user.hospital_code (or all for superadmin)
    staff_q = db.query(User)
    if scope is not None:
        staff_q = staff_q.filter(User.hospital_code == scope)
    staff = staff_q.all()
    staff_metrics = []
    for s in staff:
        # only count calls that were manually triggered (triggered_by = user)
        if eids:
            calls_made = (db.query(FollowupCall)
                          .filter(FollowupCall.enrollment_id.in_(eids),
                                  FollowupCall.triggered_by == s.id).count())
            calls_completed_by = (db.query(FollowupCall)
                                  .filter(FollowupCall.enrollment_id.in_(eids),
                                          FollowupCall.triggered_by == s.id,
                                          FollowupCall.status == "completed").count())
        else:
            calls_made = calls_completed_by = 0
        esc_acked = (db.query(Escalation)
                     .filter(Escalation.hospital_code == s.hospital_code,
                             Escalation.acked_by == s.id).count()
                     if s.hospital_code != "*" else
                     db.query(Escalation).filter(Escalation.acked_by == s.id).count())
        esc_resolved = (db.query(Escalation)
                        .filter(Escalation.hospital_code == s.hospital_code,
                                Escalation.resolved_by == s.id).count()
                        if s.hospital_code != "*" else
                        db.query(Escalation).filter(Escalation.resolved_by == s.id).count())
        enrollments_created = (db.query(Enrollment)
                               .filter(Enrollment.hospital_code == s.hospital_code,
                                       Enrollment.created_by == s.id).count()
                               if s.hospital_code != "*" else
                               db.query(Enrollment).filter(Enrollment.created_by == s.id).count())
        staff_metrics.append({
            "user_id": s.id,
            "username": s.username,
            "display_name": s.display_name,
            "role": s.role,
            "ward": s.ward,
            "calls_made": calls_made,
            "calls_completed": calls_completed_by,
            "escalations_acked": esc_acked,
            "escalations_resolved": esc_resolved,
            "enrollments_created": enrollments_created,
        })
    # sort by calls_made desc
    staff_metrics.sort(key=lambda m: (-m["calls_made"], -m["escalations_resolved"]))

    # ── 8. Top escalation reasons ────────────────────────────────────────
    all_reasons: list[str] = []
    if eids:
        for (reasons_json,) in (db.query(Escalation.reasons)
                                .filter(Escalation.enrollment_id.in_(eids)).all()):
            try:
                rs = json.loads(reasons_json or "[]")
                all_reasons.extend(rs)
            except Exception:
                pass
    reason_counts = Counter(r.strip().lower() for r in all_reasons if r)
    top_reasons = [{"reason": r, "count": c}
                   for r, c in reason_counts.most_common(10)]

    # ── 9. Recent activity (audit log) ───────────────────────────────────
    al_q = db.query(AuditLog)
    if scope is not None:
        al_q = al_q.filter(AuditLog.hospital_code == scope)
    recent_activity = (al_q.order_by(AuditLog.created_at.desc())
                       .limit(20).all())
    recent_activity_out = [{
        "id": a.id,
        "actor": a.actor,
        "action": a.action,
        "entity_id": a.entity_id,
        "created_at": a.created_at,
        "meta": json.loads(a.meta) if a.meta else None,
    } for a in recent_activity]

    # ── 10. Trend: calls per day for the last `days` days ────────────────
    trend_start = today_start - timedelta(days=days)
    trend_calls: list[dict] = []
    if eids:
        all_calls_period = (db.query(FollowupCall)
                            .filter(FollowupCall.enrollment_id.in_(eids),
                                    FollowupCall.scheduled_at >= _iso(trend_start))
                            .all())
        per_day: dict[str, Counter] = defaultdict(Counter)
        for c in all_calls_period:
            day = (c.scheduled_at or "")[:10]
            if not day:
                continue
            if c.status == "completed":
                per_day[day]["done"] += 1
            elif c.status in ("no_answer", "failed"):
                per_day[day]["missed"] += 1
            else:
                per_day[day]["pending"] += 1
        for i in range(days, -1, -1):
            d = (today_start - timedelta(days=i)).date().isoformat()
            counts = per_day.get(d, Counter())
            trend_calls.append({
                "date": d,
                "completed": counts.get("done", 0),
                "missed": counts.get("missed", 0),
                "pending": counts.get("pending", 0),
            })

    # ── 11. Hospital list (superadmin only) ─────────────────────────────
    hospitals: list[dict] = []
    if is_cross_hospital(user):
        for h in db.query(Hospital).order_by(Hospital.code).all():
            h_enroll = db.query(Enrollment).filter(
                Enrollment.hospital_code == h.code).count()
            h_esc_open = db.query(Escalation).filter(
                Escalation.hospital_code == h.code,
                Escalation.status == "open").count()
            h_staff = db.query(User).filter(
                User.hospital_code == h.code).count()
            hospitals.append({
                "code": h.code,
                "name": h.name,
                "district": h.district,
                "state": h.state,
                "active": bool(h.active),
                "enrolled_total": h_enroll,
                "open_escalations": h_esc_open,
                "staff_count": h_staff,
            })

    # ── 12. WhatNow: action list (top 5 of each) ────────────────────────
    next_calls_due_2h = []
    stale_calls = []
    if eids:
        horizon_2h = _iso(now + timedelta(hours=2))
        cutoff_24h = _iso(now - timedelta(hours=24))
        # Next calls due in 2h
        rows = (db.query(FollowupCall, Enrollment, Patient)
                .join(Enrollment, FollowupCall.enrollment_id == Enrollment.id)
                .join(Patient, Enrollment.patient_id == Patient.id)
                .filter(FollowupCall.enrollment_id.in_(eids),
                        FollowupCall.status == "pending",
                        FollowupCall.scheduled_at <= horizon_2h)
                .order_by(FollowupCall.scheduled_at).limit(5).all())
        for c, e, p in rows:
            sched = _parse_dt(c.scheduled_at) or now
            in_min = max(0, int((sched - now).total_seconds() / 60))
            next_calls_due_2h.append({
                "enrollment_id": e.id, "patient_id": p.id,
                "patient_name": p.name, "day_index": c.day_index,
                "scheduled_at": c.scheduled_at, "in_minutes": in_min,
            })
        # Stale (pending/ringing > 24h)
        rows = (db.query(FollowupCall, Enrollment, Patient)
                .join(Enrollment, FollowupCall.enrollment_id == Enrollment.id)
                .join(Patient, Enrollment.patient_id == Patient.id)
                .filter(FollowupCall.enrollment_id.in_(eids),
                        FollowupCall.status.in_(["pending", "ringing"]),
                        FollowupCall.scheduled_at < cutoff_24h)
                .order_by(FollowupCall.scheduled_at).limit(5).all())
        for c, e, p in rows:
            sched = _parse_dt(c.scheduled_at) or now
            hours = int((now - sched).total_seconds() / 3600)
            stale_calls.append({
                "enrollment_id": e.id, "patient_id": p.id,
                "patient_name": p.name, "status": c.status,
                "scheduled_at": c.scheduled_at, "hours_stale": hours,
            })

    unresolved_red = []
    if scope is not None or is_cross_hospital(user):
        cutoff_1h = _iso(now - timedelta(hours=1))
        esc_open_q = db.query(Escalation).filter(Escalation.status == "open")
        esc_acked_q = db.query(Escalation).filter(
            Escalation.status == "acked", Escalation.acked_at < cutoff_1h)
        if scope is not None:
            esc_open_q = esc_open_q.filter(Escalation.hospital_code == scope)
            esc_acked_q = esc_acked_q.filter(Escalation.hospital_code == scope)
        for x in (esc_open_q.order_by(Escalation.created_at)
                          .limit(5).all() +
                  esc_acked_q.order_by(Escalation.acked_at)
                              .limit(5).all()):
            e = db.get(Enrollment, x.enrollment_id)
            p = db.get(Patient, e.patient_id) if e else None
            created = _parse_dt(x.created_at) or now
            age_min = int((now - created).total_seconds() / 60)
            unresolved_red.append({
                "escalation_id": x.id,
                "patient_name": p.name if p else "?",
                "enrollment_id": x.enrollment_id,
                "patient_id": p.id if p else None,
                "status": x.status,
                "level": x.level,
                "age_minutes": age_min,
            })

    # ── 13. System health (superadmin only) ─────────────────────────────
    system_health: dict | None = None
    if is_cross_hospital(user):
        system_health = {
            "hospitals_total": db.query(Hospital).count(),
            "hospitals_active": db.query(Hospital).filter(Hospital.active == 1).count(),
            "users_total": db.query(User).count(),
            "users_by_role": dict(Counter(
                s.role for s in db.query(User.role).all())),
            "patients_total": db.query(Patient).count(),
            "enrollments_total": db.query(Enrollment).count(),
            "calls_total": db.query(FollowupCall).count(),
            "calls_in_progress": db.query(FollowupCall)
                .filter(FollowupCall.status == "in_progress").count(),
            "escalations_total": db.query(Escalation).count(),
            "audit_log_total": db.query(AuditLog).count(),
        }

    return {
        # Scope metadata
        "scope": {
            "role": user.role,
            "hospital_code": user.hospital_code,
            "is_superadmin": is_cross_hospital(user),
            "queried_hospital": scope,
            "period_days": days,
            "generated_at": _iso(now),
        },
        # Top-line KPIs
        "kpis": {
            "patients_total": patients_total,
            "new_patients_today": new_patients_today,
            "enrolled_total": enrolled_total,
            "enrolled_active": enrolled_active,
            "enrolled_completed": enrolled_completed,
            "enrolled_cancelled": enrolled_cancelled,
            "calls_total": calls_total,
            "calls_today": calls_today,
            "calls_this_week": calls_this_week,
            "calls_this_month": calls_this_month,
            "calls_completed": calls_completed,
            "calls_no_answer": calls_no_answer,
            "calls_in_progress": calls_in_progress,
            "calls_ringing": calls_ringing,
            "calls_pending": calls_pending,
            "reach_rate": reach_rate,
            "risk_green": risk_green,
            "risk_yellow": risk_yellow,
            "risk_red": risk_red,
            "escalations_total": esc_total,
            "escalations_open": esc_open,
            "escalations_acked": esc_acked,
            "escalations_resolved": esc_resolved,
            "escalations_resolved_today": esc_resolved_today,
            "escalations_open_today": esc_open_today,
            "avg_ack_hours": avg_ack_hours,
            "avg_resolve_hours": avg_resolve_hours,
        },
        # Breakdowns
        "outcomes": outcomes,
        "ward_breakdown": ward_breakdown,
        "protocol_breakdown": protocol_breakdown,
        "staff_metrics": staff_metrics,
        "top_escalation_reasons": top_reasons,
        # Time-series
        "daily_call_trend": trend_calls,
        # Activity
        "recent_activity": recent_activity_out,
        # Action lists
        "whatnow": {
            "next_calls_due_2h": next_calls_due_2h,
            "stale_calls": stale_calls,
            "unresolved_red": unresolved_red,
        },
        # Superadmin-only
        "hospitals": hospitals if is_cross_hospital(user) else None,
        "system_health": system_health,
    }


@router.get("/activity")
def activity_feed(
    limit: int = Query(50, ge=1, le=500),
    action: str | None = Query(None, description="Filter to one action type"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Recent audit-log activity. The full audit log of who-did-what.
    Superadmin can filter by `hospital`; other roles are locked to theirs.
    """
    q = db.query(AuditLog)
    if user.hospital_code != "*":
        q = q.filter(AuditLog.hospital_code == user.hospital_code)
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [{
        "id": a.id,
        "hospital_code": a.hospital_code,
        "actor": a.actor,
        "action": a.action,
        "entity_id": a.entity_id,
        "created_at": a.created_at,
        "meta": json.loads(a.meta) if a.meta else None,
    } for a in rows]
