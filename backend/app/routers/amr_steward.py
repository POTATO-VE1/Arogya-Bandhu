"""AMR stewardship API — manual trigger for demo + live status."""
"""AMR stewardship API — manual trigger for demo + live status + summary KPIs."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app.deps import current_user
from app.models import Enrollment, EnrollmentMed, Escalation, FollowupCall, User

router = APIRouter(tags=["amr-steward"])


@router.post("/api/amr/steward/trigger")
def trigger_steward(user: User = Depends(current_user)):
    from app.amr_steward import run_full_steward_cycle
    result = run_full_steward_cycle()
    return result


@router.get("/api/amr/steward/status")
def steward_status(user: User = Depends(current_user)):
    from app.amr_steward import (
        _reminded_today, _pill_count_sent, _pill_count_response, _meds_confirmed,
    )
    return {
        "reminders_today": len(_reminded_today),
        "pill_checks_sent": len(_pill_count_sent),
        "pill_responses": len(_pill_count_response),
        "meds_confirmed": len(_meds_confirmed),
    }


@router.get("/api/amr/summary")
def amr_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Full AMR stewardship KPI dashboard data."""
    hc = user.hospital_code

    total_enrolled = db.query(Enrollment).filter(
        Enrollment.hospital_code == hc, Enrollment.status == "active"
    ).count()

    total_abx = (db.query(EnrollmentMed).join(Enrollment)
                 .filter(Enrollment.hospital_code == hc,
                         Enrollment.status == "active",
                         EnrollmentMed.med_type == "antibiotic").count())

    # AWaRe distribution
    aware_counts = (db.query(EnrollmentMed.aware_category, func.count())
                    .join(Enrollment)
                    .filter(Enrollment.hospital_code == hc,
                            Enrollment.status == "active",
                            EnrollmentMed.med_type == "antibiotic",
                            EnrollmentMed.aware_category.isnot(None))
                    .group_by(EnrollmentMed.aware_category)
                    .all())
    aware_dist = {cat: cnt for cat, cnt in aware_counts}

    # call stats
    total_calls = db.query(FollowupCall).join(Enrollment).filter(
        Enrollment.hospital_code == hc).count()
    completed_calls = db.query(FollowupCall).join(Enrollment).filter(
        Enrollment.hospital_code == hc,
        FollowupCall.status == "completed").count()
    no_answer_calls = db.query(FollowupCall).join(Enrollment).filter(
        Enrollment.hospital_code == hc,
        FollowupCall.status.in_(["no_answer", "failed"])).count()

    reach_rate = round(completed_calls / max(completed_calls + no_answer_calls, 1) * 100, 1)

    # escalations
    open_escalations = db.query(Escalation).join(Enrollment).filter(
        Enrollment.hospital_code == hc,
        Escalation.status == "open").count()

    # risk distribution
    risk_dist_rows = (db.query(FollowupCall.risk_level, func.count())
                      .join(Enrollment)
                      .filter(Enrollment.hospital_code == hc,
                              FollowupCall.status == "completed",
                              FollowupCall.risk_level.isnot(None))
                      .group_by(FollowupCall.risk_level)
                      .all())
    risk_dist = {level: cnt for level, cnt in risk_dist_rows}

    # AMR stewardship status
    from app.amr_steward import (
        _reminded_today, _pill_count_sent, _pill_count_response, _meds_confirmed,
    )

    return {
        "total_enrolled": total_enrolled,
        "antibiotic_patients": total_abx,
        "aware_distribution": {
            "Access": aware_dist.get("Access", 0),
            "Watch": aware_dist.get("Watch", 0),
            "Reserve": aware_dist.get("Reserve", 0),
        },
        "calls_total": total_calls,
        "calls_completed": completed_calls,
        "calls_no_answer": no_answer_calls,
        "reach_rate": reach_rate,
        "open_escalations": open_escalations,
        "risk_distribution": {
            "green": risk_dist.get("green", 0),
            "yellow": risk_dist.get("yellow", 0),
            "red": risk_dist.get("red", 0),
        },
        "stewardship": {
            "reminders_sent_today": len(_reminded_today),
            "pill_checks_sent": len(_pill_count_sent),
            "pill_responses": len(_pill_count_response),
            "meds_confirmed": len(_meds_confirmed),
        },
    }
