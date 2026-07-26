"""Health analytics — trend detection, anomaly flags, composite health score.

Pure computation over stored patient_health_data rows.
No external calls — reads from DB, returns structured results.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("health_fit.analytics")

# ── Normal ranges (adults) ───────────────────────────────────────────────────
NORMAL_RANGES = {
    "heart_rate": {"low": 50, "high": 100, "resting_low": 50, "resting_high": 80},
    "spo2": {"low": 94, "high": 100},
    "steps": {"sedentary": 2000, "moderate": 5000, "active": 8000},
    "sleep": {"short": 360, "normal_min": 420, "normal_max": 540, "long": 600},  # minutes
    "body_temp": {"low": 36.1, "high": 37.5, "fever": 38.0},
    "weight": {"drop_concern_pct": 5},  # >5% weight drop in 7 days is concerning
}


def compute_health_summary(rows: list[dict]) -> dict:
    """Compute a per-patient health summary from stored health data rows.

    Input rows: [{"metric_type": str, "value": float, "recorded_at": str}, ...]
    Output: structured summary dict.
    """
    # Group by metric type
    by_metric: dict[str, list[dict]] = {}
    for r in rows:
        mt = r["metric_type"]
        by_metric.setdefault(mt, []).append(r)

    summary = {}

    # ── Heart Rate ────────────────────────────────────────────────────────
    if "heart_rate" in by_metric:
        vals = [r["value"] for r in by_metric["heart_rate"]]
        summary["heart_rate"] = {
            "latest": vals[-1] if vals else None,
            "avg_7d": round(sum(vals[-7:]) / min(len(vals), 7), 1) if vals else None,
            "min_7d": round(min(vals[-7:]), 1) if vals else None,
            "max_7d": round(max(vals[-7:]), 1) if vals else None,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_heart_rate(vals),
        }

    # ── SpO2 ─────────────────────────────────────────────────────────────
    if "spo2" in by_metric:
        vals = [r["value"] for r in by_metric["spo2"]]
        summary["spo2"] = {
            "latest": vals[-1] if vals else None,
            "avg_7d": round(sum(vals[-7:]) / min(len(vals), 7), 1) if vals else None,
            "min_7d": round(min(vals[-7:]), 1) if vals else None,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_spo2(vals),
        }

    # ── Steps ────────────────────────────────────────────────────────────
    if "steps" in by_metric:
        vals = [r["value"] for r in by_metric["steps"]]
        summary["steps"] = {
            "today": vals[-1] if vals else None,
            "avg_7d": round(sum(vals[-7:]) / min(len(vals), 7)) if vals else None,
            "total_7d": sum(vals[-7:]) if vals else 0,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_steps(vals),
        }

    # ── Sleep ────────────────────────────────────────────────────────────
    if "sleep" in by_metric:
        vals = [r["value"] for r in by_metric["sleep"]]
        summary["sleep"] = {
            "latest_hours": round(vals[-1] / 60, 1) if vals else None,
            "avg_hours": round(sum(vals[-7:]) / min(len(vals), 7) / 60, 1) if vals else None,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_sleep(vals),
        }

    # ── Body Temperature ─────────────────────────────────────────────────
    if "body_temp" in by_metric:
        vals = [r["value"] for r in by_metric["body_temp"]]
        summary["body_temp"] = {
            "latest": vals[-1] if vals else None,
            "avg_7d": round(sum(vals[-7:]) / min(len(vals), 7), 1) if vals else None,
            "max_7d": round(max(vals[-7:]), 1) if vals else None,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_body_temp(vals),
        }

    # ── Weight ───────────────────────────────────────────────────────────
    if "weight" in by_metric:
        vals = [r["value"] for r in by_metric["weight"]]
        summary["weight"] = {
            "latest": vals[-1] if vals else None,
            "avg_7d": round(sum(vals[-7:]) / min(len(vals), 7), 1) if vals else None,
            "count": len(vals),
            "trend": _compute_trend(vals),
            "flags": _flag_weight(vals),
        }

    # ── Composite health score (0-100) ───────────────────────────────────
    summary["health_score"] = _compute_composite_score(summary)
    summary["overall_flags"] = _collect_all_flags(summary)

    return summary


def _compute_trend(values: list[float]) -> str:
    """Simple trend from last 7 values: 'improving', 'stable', 'declining', 'insufficient'."""
    if len(values) < 3:
        return "insufficient"
    recent = values[-7:] if len(values) >= 7 else values
    mid = len(recent) // 2
    first_half = sum(recent[:mid]) / mid
    second_half = sum(recent[mid:]) / len(recent[mid:])
    pct_change = ((second_half - first_half) / first_half * 100) if first_half else 0
    if pct_change > 5:
        return "improving"
    elif pct_change < -5:
        return "declining"
    return "stable"


def _flag_heart_rate(vals: list[float]) -> list[str]:
    flags = []
    latest = vals[-1] if vals else None
    if latest is not None:
        if latest > NORMAL_RANGES["heart_rate"]["high"]:
            flags.append("elevated_hr")
        if latest < NORMAL_RANGES["heart_rate"]["low"]:
            flags.append("low_hr")
    # Check for sudden spike (last reading > 30% above 7-day avg)
    if len(vals) >= 7:
        avg = sum(vals[-7:]) / 7
        if vals[-1] > avg * 1.3:
            flags.append("hr_spike")
    return flags


def _flag_spo2(vals: list[float]) -> list[str]:
    flags = []
    latest = vals[-1] if vals else None
    if latest is not None and latest < NORMAL_RANGES["spo2"]["low"]:
        flags.append("low_spo2")
    # Check for drop
    if len(vals) >= 3:
        recent_avg = sum(vals[-3:]) / 3
        if recent_avg < NORMAL_RANGES["spo2"]["low"]:
            flags.append("sustained_low_spo2")
    return flags


def _flag_steps(vals: list[float]) -> list[str]:
    flags = []
    if vals:
        today = vals[-1]
        if today < NORMAL_RANGES["steps"]["sedentary"]:
            flags.append("sedentary")
        # Check for 3+ zero-activity days
        recent = vals[-7:] if len(vals) >= 7 else vals
        zero_days = sum(1 for v in recent if v < 100)
        if zero_days >= 3:
            flags.append("prolonged_inactivity")
    return flags


def _flag_sleep(vals: list[float]) -> list[str]:
    flags = []
    latest = vals[-1] if vals else None
    if latest is not None:
        if latest < NORMAL_RANGES["sleep"]["short"]:
            flags.append("sleep_deprivation")
        if latest > NORMAL_RANGES["sleep"]["long"]:
            flags.append("excessive_sleep")
    return flags


def _flag_body_temp(vals: list[float]) -> list[str]:
    flags = []
    latest = vals[-1] if vals else None
    if latest is not None:
        if latest >= NORMAL_RANGES["body_temp"]["fever"]:
            flags.append("fever")
        elif latest >= NORMAL_RANGES["body_temp"]["high"]:
            flags.append("low_grade_fever")
    return flags


def _flag_weight(vals: list[float]) -> list[str]:
    flags = []
    if len(vals) >= 2:
        oldest = vals[0]
        newest = vals[-1]
        if oldest > 0:
            pct_change = ((newest - oldest) / oldest) * 100
            if abs(pct_change) > NORMAL_RANGES["weight"]["drop_concern_pct"]:
                flags.append("significant_weight_change")
    return flags


def _compute_composite_score(summary: dict) -> int:
    """Compute a 0-100 health score. Higher = healthier.

    Scoring:
    - SpO2: 30 points (most critical)
    - Heart rate: 25 points
    - Activity (steps): 20 points
    - Sleep: 15 points
    - Body temp: 10 points
    """
    score = 50  # start at 50 (neutral)

    # SpO2 (±15 from baseline)
    if "spo2" in summary:
        spo2 = summary["spo2"].get("latest")
        if spo2 is not None:
            if spo2 >= 97:
                score += 15
            elif spo2 >= 94:
                score += 5
            elif spo2 >= 90:
                score -= 10
            else:
                score -= 15

    # Heart rate (±12)
    if "heart_rate" in summary:
        hr = summary["heart_rate"].get("latest")
        if hr is not None:
            if 50 <= hr <= 80:
                score += 12
            elif 80 < hr <= 100:
                score += 3
            elif hr > 100:
                score -= 10
            elif hr < 50:
                score -= 8

    # Steps (±10)
    if "steps" in summary:
        steps = summary["steps"].get("today")
        if steps is not None:
            if steps >= 5000:
                score += 10
            elif steps >= 2000:
                score += 3
            else:
                score -= 5

    # Sleep (±8)
    if "sleep" in summary:
        sleep_h = summary["sleep"].get("latest_hours")
        if sleep_h is not None:
            if 6 <= sleep_h <= 9:
                score += 8
            elif 5 <= sleep_h < 6:
                score += 2
            elif sleep_h < 5:
                score -= 5
            elif sleep_h > 10:
                score -= 3

    # Body temp (±5)
    if "body_temp" in summary:
        temp = summary["body_temp"].get("latest")
        if temp is not None:
            if temp < 37.5:
                score += 5
            elif temp < 38.0:
                score += 1
            else:
                score -= 10

    return max(0, min(100, score))


def _collect_all_flags(summary: dict) -> list[str]:
    """Collect all flags from all metrics into a single list."""
    all_flags = []
    for metric in ("heart_rate", "spo2", "steps", "sleep", "body_temp", "weight"):
        if metric in summary:
            all_flags.extend(summary[metric].get("flags", []))
    return all_flags


def compute_trajectory(rows: list[dict], days: int = 14) -> dict:
    """Compute recovery trajectory: current week vs previous week.

    Returns comparison of averages, direction, and narrative.
    """
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    current_week = [r for r in rows if r["recorded_at"] >= week_ago.isoformat()]
    prev_week = [r for r in rows if two_weeks_ago.isoformat() <= r["recorded_at"] < week_ago.isoformat()]

    def avg_by_metric(data: list[dict], metric: str) -> float | None:
        vals = [r["value"] for r in data if r["metric_type"] == metric]
        return round(sum(vals) / len(vals), 1) if vals else None

    trajectory = {}
    for metric in ("heart_rate", "spo2", "steps", "sleep", "body_temp"):
        cur = avg_by_metric(current_week, metric)
        prev = avg_by_metric(prev_week, metric)
        if cur is not None and prev is not None:
            diff = cur - prev
            pct = round((diff / prev) * 100, 1) if prev else 0
            trajectory[metric] = {
                "current_week_avg": cur,
                "previous_week_avg": prev,
                "change": round(diff, 1),
                "change_pct": pct,
                "direction": "improving" if _is_improving(metric, diff) else "declining" if _is_declining(metric, diff) else "stable",
            }

    return trajectory


def _is_improving(metric: str, diff: float) -> bool:
    """For most metrics, lower is better (HR, temp) or higher is better (SpO2, steps)."""
    if metric in ("heart_rate", "body_temp"):
        return diff < -2  # lower HR/temp = better
    return diff > 100 if metric == "steps" else diff > 0.5  # higher SpO2/steps/sleep = better


def _is_declining(metric: str, diff: float) -> bool:
    if metric in ("heart_rate", "body_temp"):
        return diff > 2
    return diff < -100 if metric == "steps" else diff < -0.5
