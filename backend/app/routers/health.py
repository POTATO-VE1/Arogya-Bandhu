"""Health device integration — Google Fit OAuth + data sync + dashboard.

Endpoints:
  /api/health/fit/authorize  — starts Google OAuth flow (redirects to Google)
  /api/health/fit/callback   — OAuth callback (exchanges code, stores tokens)
  /api/health/sync            — trigger immediate data fetch for a patient
  /api/patients/{id}/health-data   — per-patient health metrics
  /api/patients/{id}/health-summary — computed analytics
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.db import get_db, now_utc
from app.deps import current_user
from app.health_fit.analytics import compute_health_summary, compute_trajectory
from app.health_fit.client import fetch_all_metrics, test_connection
from app.health_fit import PatientHealthData, PatientHealthToken
from app.health_fit.oauth import exchange_code, get_authorize_url, refresh_access_token
from app.models import Enrollment, Patient

log = logging.getLogger("routers.health")
router = APIRouter(prefix="/api", tags=["health"])

# ── Fernet encryption for tokens ─────────────────────────────────────────────
_fernet = None


def _get_fernet():
    """Lazy-init Fernet for token encryption."""
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
        key = settings.HEALTH_ENCRYPT_KEY
        if not key:
            # Auto-generate from SECRET_KEY (deterministic per instance)
            import hashlib
            key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
            key_b64 = __import__("base64").urlsafe_b64encode(key).decode()
            _fernet = Fernet(key_b64.encode())
        else:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except ImportError:
        log.warning("cryptography package not installed — token encryption disabled")
        _fernet = None
    return _fernet


def _encrypt(text: str) -> str:
    f = _get_fernet()
    if f is None:
        return text  # no encryption fallback
    return f.encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    f = _get_fernet()
    if f is None:
        return token
    return f.decrypt(token.encode()).decode()


# ── OAuth Flow ───────────────────────────────────────────────────────────────


@router.get("/health/fit/authorize")
def fit_authorize(tgid: int = Query(..., description="Telegram user ID")):
    """Start Google Fit OAuth flow. Redirects to Google consent screen."""
    if not settings.GOOGLE_FIT_CLIENT_ID:
        raise HTTPException(503, "Google Fit integration not configured")

    # Build redirect URI from PUBLIC_BASE_URL
    redirect_uri = f"{settings.PUBLIC_BASE_URL}/api/health/fit/callback"
    url = get_authorize_url(tgid, redirect_uri)
    return RedirectResponse(url)


@router.get("/health/fit/callback")
def fit_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: DBSession = Depends(get_db),
) -> HTMLResponse:
    """OAuth callback from Google. Exchanges code for tokens, stores them."""
    if not settings.GOOGLE_FIT_CLIENT_ID:
        return HTMLResponse("<h1>Google Fit not configured</h1>", status_code=503)

    redirect_uri = f"{settings.PUBLIC_BASE_URL}/api/health/fit/callback"
    token_data = exchange_code(code, state, redirect_uri)

    if not token_data or "access_token" not in token_data:
        return HTMLResponse(
            "<h1>Authorization Failed</h1>"
            "<p>Could not exchange authorization code. Please try again.</p>"
            "<p><a href='javascript:window.close()'>Close this window</a></p>",
            status_code=400,
        )

    # Parse state: "telegram_id:random_state"
    try:
        telegram_id_str, _ = state.split(":", 1)
        telegram_id = int(telegram_id_str)
    except (ValueError, TypeError):
        return HTMLResponse("<h1>Invalid state parameter</h1>", status_code=400)

    # Store tokens — we need to find the patient by telegram_id
    # For now, we'll store the telegram_id mapping and resolve it later
    # The patient will be linked when they /connect_device in Telegram
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expiry_seconds = token_data.get("expires_in", 3600)
    expiry_at = (datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)).isoformat()
    scope = token_data.get("scope", "")

    # Test connection
    user_info = test_connection(access_token)

    # Store as pending connection (will be linked to patient on next /connect_device)
    _pending_connections[telegram_id] = {
        "access_token": _encrypt(access_token),
        "refresh_token": _encrypt(refresh_token),
        "expiry": expiry_at,
        "scope": scope,
        "connected_at": now_utc(),
        "user_info": user_info,
    }

    # T12 follow-up: notify the patient via Telegram that the link succeeded.
    # Previously this was a silent dead-end — the user clicked the OAuth
    # link, returned to Telegram, and got no confirmation.
    try:
        from app.telegram.bot import notify_google_fit_linked
        notify_google_fit_linked(telegram_id)
    except Exception as e:
        log.warning("notify_google_fit_linked failed: %s", e)

    display_name = user_info.get("displayName", "your") if user_info else "your"
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;text-align:center;padding:50px;"
        f"background:#1a1a2e;color:#e0e0e0;'>"
        f"<h1 style='color:#00ff88;'>[OK] Google Fit Connected!</h1>"
        f"<p>Authorized for {display_name} account.</p>"
        f"<p>Return to Telegram and send <b>/connect_device</b> to finish linking.</p>"
        f"<script>setTimeout(() => window.close(), 5000);</script>"
        f"<p><a href='javascript:window.close()' style='color:#00ff88;'>Close this window</a></p>"
        f"</body></html>",
        status_code=200,
    )


# Pending OAuth connections: telegram_id → token data
_pending_connections: dict[int, dict] = {}


def get_pending_connection(telegram_id: int) -> dict | None:
    """Check if a Telegram user has a pending OAuth connection."""
    return _pending_connections.pop(telegram_id, None)


# ── Sync endpoint ────────────────────────────────────────────────────────────


@router.post("/health/sync")
def sync_health_data(
    patient_id: str = Query(..., description="Patient UUID"),
    days: int = Query(7, ge=1, le=30, description="Days to fetch"),
    db: DBSession = Depends(get_db),
    user=Depends(current_user),
):
    """Trigger immediate Google Fit data fetch for a patient."""
    # Verify patient exists and is in same hospital
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.hospital_code == user.hospital_code,
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")

    # Get stored tokens
    token_row = db.query(PatientHealthToken).filter(
        PatientHealthToken.patient_id == patient_id,
        PatientHealthToken.provider == "google_fit",
    ).first()
    if not token_row:
        raise HTTPException(404, "No Google Fit connection found for this patient")

    # Check if token needs refresh
    access_token = _decrypt(token_row.access_token)
    if datetime.fromisoformat(token_row.token_expiry) < datetime.now(timezone.utc):
        refreshed = refresh_access_token(token_row.refresh_token, _get_fernet())
        if not refreshed:
            raise HTTPException(401, "Token expired and refresh failed — patient must reconnect")
        access_token = refreshed["access_token"]
        token_row.access_token = _encrypt(access_token)
        token_row.token_expiry = (
            datetime.now(timezone.utc) + timedelta(seconds=refreshed.get("expires_in", 3600))
        ).isoformat()
        db.commit()

    # Fetch data from Google Fit
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    metrics = fetch_all_metrics(access_token, start.isoformat(), end.isoformat())

    # Store in DB
    stored_count = 0
    for metric_type, points in metrics.items():
        for point in points:
            try:
                row = PatientHealthData(
                    id=uuid.uuid4().hex,
                    patient_id=patient_id,
                    hospital_code=user.hospital_code,
                    metric_type=metric_type,
                    value=point["value"],
                    unit={"heart_rate": "bpm", "spo2": "%", "steps": "count",
                          "sleep": "minutes", "body_temp": "°C", "weight": "kg"}.get(metric_type, ""),
                    recorded_at=point["recorded_at"],
                    source=point.get("source", "google_fit"),
                )
                db.add(row)
                stored_count += 1
            except Exception:
                pass  # skip duplicates (UNIQUE constraint)
    db.commit()

    # Update last_synced_at
    token_row.last_synced_at = now_utc()
    db.commit()

    return {"synced": stored_count, "metrics": list(metrics.keys())}


# ── Health data endpoint ─────────────────────────────────────────────────────


@router.get("/patients/{patient_id}/health-data")
def get_health_data(
    patient_id: str,
    days: int = Query(14, ge=1, le=90),
    db: DBSession = Depends(get_db),
    user=Depends(current_user),
):
    """Get per-patient health metrics for the last N days."""
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.hospital_code == user.hospital_code,
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = db.query(PatientHealthData).filter(
        PatientHealthData.patient_id == patient_id,
        PatientHealthData.recorded_at >= cutoff,
    ).order_by(PatientHealthData.recorded_at.asc()).all()

    return {
        "patient_id": patient_id,
        "patient_name": patient.name,
        "days": days,
        "data_points": len(rows),
        "connected": db.query(PatientHealthToken).filter(
            PatientHealthToken.patient_id == patient_id,
        ).first() is not None,
        "metrics": [
            {
                "metric_type": r.metric_type,
                "value": r.value,
                "unit": r.unit,
                "recorded_at": r.recorded_at,
                "source": r.source,
            }
            for r in rows
        ],
    }


# ── Health summary endpoint ──────────────────────────────────────────────────


@router.get("/patients/{patient_id}/health-summary")
def get_health_summary(
    patient_id: str,
    db: DBSession = Depends(get_db),
    user=Depends(current_user),
):
    """Get computed health analytics summary for a patient."""
    patient = db.query(Patient).filter(
        Patient.id == patient_id,
        Patient.hospital_code == user.hospital_code,
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = db.query(PatientHealthData).filter(
        PatientHealthData.patient_id == patient_id,
        PatientHealthData.recorded_at >= cutoff,
    ).order_by(PatientHealthData.recorded_at.asc()).all()

    row_dicts = [
        {"metric_type": r.metric_type, "value": r.value, "recorded_at": r.recorded_at}
        for r in rows
    ]

    summary = compute_health_summary(row_dicts)
    trajectory = compute_trajectory(row_dicts)

    return {
        "patient_id": patient_id,
        "patient_name": patient.name,
        "summary": summary,
        "trajectory": trajectory,
        "data_points": len(rows),
    }


# ── Dashboard endpoint ───────────────────────────────────────────────────────


@router.get("/health/dashboard")
def health_dashboard(
    db: DBSession = Depends(get_db),
    user=Depends(current_user),
):
    """Overview of all patients' health data for the doctor dashboard."""
    # Get all active enrollments with connected devices
    patients = db.query(Patient).filter(
        Patient.hospital_code == user.hospital_code,
    ).all()

    dashboard = []
    for patient in patients:
        token = db.query(PatientHealthToken).filter(
            PatientHealthToken.patient_id == patient.id,
        ).first()

        if not token:
            continue  # skip patients without connected devices

        # Get latest data points
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = db.query(PatientHealthData).filter(
            PatientHealthData.patient_id == patient.id,
            PatientHealthData.recorded_at >= cutoff,
        ).all()

        row_dicts = [
            {"metric_type": r.metric_type, "value": r.value, "recorded_at": r.recorded_at}
            for r in rows
        ]
        summary = compute_health_summary(row_dicts)

        # Get enrollment info
        enrollment = db.query(Enrollment).filter(
            Enrollment.patient_id == patient.id,
            Enrollment.status == "active",
        ).first()

        dashboard.append({
            "patient_id": patient.id,
            "patient_name": patient.name,
            "condition": enrollment.condition_label if enrollment else "unknown",
            "last_synced": token.last_synced_at,
            "health_score": summary.get("health_score", 0),
            "flags": summary.get("overall_flags", []),
            "latest_vitals": {
                "hr": summary.get("heart_rate", {}).get("latest"),
                "spo2": summary.get("spo2", {}).get("latest"),
                "steps": summary.get("steps", {}).get("today"),
                "temp": summary.get("body_temp", {}).get("latest"),
            },
        })

    # Sort by health score (lowest first = most concerning)
    dashboard.sort(key=lambda x: x["health_score"])

    return {
        "total_connected": len(dashboard),
        "patients": dashboard,
    }
